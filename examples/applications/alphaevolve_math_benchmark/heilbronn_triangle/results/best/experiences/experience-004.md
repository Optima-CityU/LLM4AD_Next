Insight: Use a soft-min surrogate with a short projected backtracking line search and annealed fallback inside multi-point updates to align step length with local curvature and maintain efficient evaluations.

- Soft-min Guided Backtracking Line Search for Multi-point Updates: uses a projected backtracking line search over step scales {1.0, 0.6, 0.36, 0.216} along the soft-min weighted multi-point subgradient direction and accepts the first candidate that increases the soft-min surrogate S, otherwise using simulated annealing acceptance with probability exp((S_new - S_old)/T_soft) or rejecting the proposal.
- Soft-min Guided Backtracking Line Search for Multi-point Updates: computes S = -(1/α) · log(Σ_t exp(-α · A_t)) over the same worst-triangle pool used for the gradient and re-evaluates only triangles incident to moved points to keep the line search inexpensive while addressing competing bottleneck triangles and projection-induced distortions.
- Soft-min Guided Backtracking Line Search for Multi-point Updates: retains the existing α-schedule tied to annealing temperature, per-point direction normalization, and barycentric interior projection, and switches the multi-point acceptance rule from min-area-only to the consistent soft-min surrogate S to better match local curvature.
- Soft-min Guided Backtracking Line Search for Multi-point Updates: achieved target_ratio 0.9364068498910147 with min_area 0.034178850021022035 and validity 1.0 on the n=11 Heilbronn benchmark, demonstrating successful performance under these evaluation conditions.

```python
#!/usr/bin/env python3
"""Heilbronn problem solver for 11 points in an equilateral triangle.

We maximize the minimum triangle area formed by any three of the points.
The algorithm combines worst-triangle-guided local moves, simulated annealing,
critical-triple subgradient ascent, and multiple restarts, while strictly
keeping all points inside the triangle via barycentric coordinates.

Triangle vertices:
A = (0, 0)
B = (1, 0)
C = (0.5, sqrt(3)/2)
"""

import json
import itertools
import math
import random
import heapq


# EVOLVE_START
def run_search_point(n=11):
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")

    # Geometry constants for the reference equilateral triangle
    SQRT3 = math.sqrt(3.0)
    TRI_HEIGHT = SQRT3 * 0.5
    AREA_TRI = SQRT3 / 4.0  # area of the big triangle

    # Vertices of the equilateral triangle
    A = (0.0, 0.0)
    B = (1.0, 0.0)
    C = (0.5, TRI_HEIGHT)

    # Index combinations for triangles with n points
    def all_triangle_indices(npts):
        return list(itertools.combinations(range(npts), 3))

    # Barycentric <-> Cartesian conversions.
    # Barycentric (u, v, w) requires u + v + w = 1 and u, v, w > 0 for interior.
    def bary_to_cart(u, v, w):
        # P = u*A + v*B + w*C; with A=(0,0), B=(1,0), C=(0.5,h)
        x = v + 0.5 * w
        y = TRI_HEIGHT * w
        return x, y

    def cart_to_bary(x, y):
        # From y: w = 2*y/sqrt(3)
        w = (2.0 / SQRT3) * y
        # From x: x = v + 0.5*w -> v = x - 0.5*w
        v = x - 0.5 * w
        # u = 1 - v - w
        u = 1.0 - v - w
        return u, v, w

    def clip_bary_interior(u, v, w, eps=1e-6):
        # Project onto barycentric simplex with lower bound eps for strict interior.
        # The map: clamp to >= eps, then renormalize to sum 1.
        u = max(u, eps)
        v = max(v, eps)
        w = max(w, eps)
        s = u + v + w
        if s <= 0:
            u = v = w = 1.0 / 3.0
            u = max(u, eps)
            v = max(v, eps)
            w = max(w, eps)
            s = u + v + w
        u /= s
        v /= s
        w /= s
        return u, v, w

    def ensure_point_inside(x, y, eps=1e-6):
        u, v, w = cart_to_bary(x, y)
        u, v, w = clip_bary_interior(u, v, w, eps=eps)
        return bary_to_cart(u, v, w)

    def cross(ax, ay, bx, by):
        return ax * by - ay * bx

    def tri_area_norm(p1, p2, p3):
        # area normalized by AREA_TRI
        x1, y1 = p1
        x2, y2 = p2
        x3, y3 = p3
        area2 = abs((x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1))  # 2*area
        area = 0.5 * area2
        return area / AREA_TRI

    def tri_area_signed_and_grads_norm(p1, p2, p3):
        # Signed normalized area and gradients of absolute area.
        # Signed area As = 0.5 * ((x2-x1)(y3-y1) - (y2-y1)(x3-x1))
        # Grad of signed area w.r.t p1: 0.5 * (y2 - y3, x3 - x2)
        # w.r.t p2: 0.5 * (y3 - y1, x1 - x3)
        # w.r.t p3: 0.5 * (y1 - y2, x2 - x1)
        x1, y1 = p1
        x2, y2 = p2
        x3, y3 = p3
        f = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)
        As = 0.5 * f
        As_norm = As / AREA_TRI
        # sign for absolute area subgradient
        sgn = 1.0 if As >= 0.0 else -1.0
        # grads of absolute normalized area
        scale = 0.5 * sgn / AREA_TRI
        g1x = scale * (y2 - y3)
        g1y = scale * (x3 - x2)
        g2x = scale * (y3 - y1)
        g2y = scale * (x1 - x3)
        g3x = scale * (y1 - y2)
        g3y = scale * (x2 - x1)
        return As_norm, (g1x, g1y), (g2x, g2y), (g3x, g3y)

    def min_distance_to_others(points, idx):
        xi, yi = points[idx]
        mind2 = float("inf")
        for j, (xj, yj) in enumerate(points):
            if j == idx:
                continue
            dx = xi - xj
            dy = yi - yj
            d2 = dx * dx + dy * dy
            if d2 < mind2:
                mind2 = d2
        return math.sqrt(mind2) if mind2 < float("inf") else float("inf")

    class TriAreaState:
        """Maintain triangle areas incrementally for a set of points."""
        def __init__(self, points):
            self.n = len(points)
            self.points = [tuple(p) for p in points]
            self.combos = all_triangle_indices(self.n)
            self.m = len(self.combos)
            # For each point index, list of area indices that include it
            self.combos_by_point = [[] for _ in range(self.n)]
            for ti, (i, j, k) in enumerate(self.combos):
                self.combos_by_point[i].append(ti)
                self.combos_by_point[j].append(ti)
                self.combos_by_point[k].append(ti)
            self.areas = [0.0] * self.m
            self.recompute_all()

        def recompute_all(self):
            for ti, (i, j, k) in enumerate(self.combos):
                self.areas[ti] = tri_area_norm(self.points[i], self.points[j], self.points[k])

        def reeval_point(self, idx):
            # Recompute areas for all triangles including point idx
            for ti in self.combos_by_point[idx]:
                i, j, k = self.combos[ti]
                self.areas[ti] = tri_area_norm(self.points[i], self.points[j], self.points[k])

        def global_min(self):
            # Return min area and its triangle index
            amin = float("inf")
            arg = -1
            for ti, a in enumerate(self.areas):
                if a < amin:
                    amin = a
                    arg = ti
            return amin, arg

        def worst_k(self, k):
            # Return list of (area, triangle_index), smallest k
            k = min(k, self.m)
            idxs = heapq.nsmallest(k, range(self.m), key=lambda ti: self.areas[ti])
            return [(self.areas[ti], ti) for ti in idxs]

    # Initialization strategies

    def farthest_point_subset(candidates, k, rng):
        # Select k points from candidates using farthest-point sampling to maximize spread
        if k >= len(candidates):
            return list(candidates)
        chosen = []
        remaining = list(candidates)
        # Start from the point farthest from triangle centroid for variety
        cx, cy = 0.5, TRI_HEIGHT / 3.0 * 2.0
        best_idx = 0
        best_d2 = -1.0
        for idx, (px, py) in enumerate(remaining):
            dx = px - cx
            dy = py - cy
            d2 = dx * dx + dy * dy
            if d2 > best_d2:
                best_d2 = d2
                best_idx = idx
        chosen.append(remaining.pop(best_idx))
        while len(chosen) < k and remaining:
            best_idx = None
            best_dist = -1.0
            for idx, p in enumerate(remaining):
                px, py = p
                md2 = float("inf")
                for (cx, cy) in chosen:
                    dx = px - cx
                    dy = py - cy
                    d2 = dx * dx + dy * dy
                    if d2 < md2:
                        md2 = d2
                if md2 > best_dist:
                    best_dist = md2
                    best_idx = idx
            if best_idx is None:
                break
            chosen.append(remaining.pop(best_idx))
        return chosen

    def grid_initial_points(npts, rng, eps=1e-6):
        # Build an interior triangular lattice using integer barycentric coords
        # with i+j+k = M and i,j,k >= 1. For M=7, count is C(6,2)=15 interior points.
        M = 7
        lattice = []
        for i in range(1, M):
            for j in range(1, M - i):
                k = M - i - j
                if k < 1:
                    continue
                u = i / M
                v = j / M
                w = k / M
                # small jitter in barycentric space to avoid degeneracy
                jb = 0.007
                du = (rng.random() - 0.5) * jb
                dv = (rng.random() - 0.5) * jb
                dw = -du - dv
                u2, v2, w2 = clip_bary_interior(u + du, v + dv, w + dw, eps=eps)
                x, y = bary_to_cart(u2, v2, w2)
                lattice.append((x, y))
        # Select npts via farthest-point sampling
        pts = farthest_point_subset(lattice, npts, rng)
        # If not enough, add random points inside
        while len(pts) < npts:
            # Random barycentric strictly inside via normalized positive samples
            a = rng.random() + 1e-6
            b = rng.random() + 1e-6
            c = rng.random() + 1e-6
            s = a + b + c
            u, v, w = a / s, b / s, c / s
            u, v, w = clip_bary_interior(u, v, w, eps=eps)
            pts.append(bary_to_cart(u, v, w))
        # Ensure strictly inside
        pts2 = [ensure_point_inside(x, y, eps=eps) for (x, y) in pts]
        return pts2

    def poisson_initial_points(npts, rng, eps=1e-6):
        # Greedy farthest-point (Poisson-like) placement with many random candidates per point
        pts = []
        # Start from centroid-ish point
        u, v, w = 0.34, 0.33, 0.33
        x, y = bary_to_cart(u, v, w)
        pts.append(ensure_point_inside(x, y, eps=eps))
        candidates_per_step = 600
        jitter_bary = 0.01
        while len(pts) < npts:
            best = None
            best_md2 = -1.0
            for _ in range(candidates_per_step):
                # Random interior barycentric
                a = rng.random() + 1e-6
                b = rng.random() + 1e-6
                c = rng.random() + 1e-6
                s = a + b + c
                u, v, w = a / s, b / s, c / s
                # Tiny barycentric jitter
                du = (rng.random() - 0.5) * 2.0 * jitter_bary
                dv = (rng.random() - 0.5) * 2.0 * jitter_bary
                dw = -du - dv
                u2, v2, w2 = clip_bary_interior(u + du, v + dv, w + dw, eps=eps)
                cx, cy = bary_to_cart(u2, v2, w2)
                # Compute min distance squared to existing pts
                md2 = float("inf")
                for (px, py) in pts:
                    dx = cx - px
                    dy = cy - py
                    d2 = dx * dx + dy * dy
                    if d2 < md2:
                        md2 = d2
                if md2 > best_md2:
                    best_md2 = md2
                    best = (cx, cy)
            if best is None:
                # Fallback: random interior
                u, v, w = clip_bary_interior(rng.random(), rng.random(), rng.random(), eps=eps)
                best = bary_to_cart(u, v, w)
            pts.append(ensure_point_inside(best[0], best[1], eps=eps))
        return pts

    # Move proposals

    def make_move_on_worst(points, combo, idx_to_move, step_size, rng, eps_inside=1e-6,
                           t_fraction=0.25, noise_fraction=0.10):
        # Move point idx_to_move to increase area of the given triangle combo
        i, j, k = combo
        others = [i, j, k]
        others.remove(idx_to_move)
        jidx, kidx = others[0], others[1]
        p = points[idx_to_move]
        pj = points[jidx]
        pk = points[kidx]

        # Base vector b = pk - pj
        bx = pk[0] - pj[0]
        by = pk[1] - pj[1]
        # Gradient direction ~ sign * (-by, bx)
        cx = p[0] - pj[0]
        cy = p[1] - pj[1]
        cross_val = cross(bx, by, cx, cy)
        sign = 1.0 if cross_val >= 0.0 else -1.0

        # Normal unit
        nx = -by
        ny = bx
        nlen = math.hypot(nx, ny)
        if nlen == 0.0:
            return None  # degenerate base; skip
        nx /= nlen
        ny /= nlen
        nx *= sign
        ny *= sign

        # Tangential unit along base
        blen = math.hypot(bx, by)
        if blen == 0.0:
            return None
        tx = bx / blen
        ty = by / blen

        # Proposed displacement: primarily along normal, a little along tangent + noise
        lam = 0.5 + 0.5 * rng.random()
        sx = step_size * lam

        t_sign = -1.0 if rng.random() < 0.5 else 1.0
        ang = 2.0 * math.pi * rng.random()
        ux = math.cos(ang)
        uy = math.sin(ang)

        dx = sx * (nx + t_fraction * t_sign * tx + noise_fraction * ux)
        dy = sx * (ny + t_fraction * t_sign * ty + noise_fraction * uy)

        newx = p[0] + dx
        newy = p[1] + dy
        newx, newy = ensure_point_inside(newx, newy, eps=eps_inside)

        return newx, newy

    def random_barycentric_perturb(points, idx, radius, rng, eps=1e-6):
        # Small random perturbation in barycentric space around current point
        x, y = points[idx]
        u, v, w = cart_to_bary(x, y)
        du = (rng.random() - 0.5) * 2.0 * radius
        dv = (rng.random() - 0.5) * 2.0 * radius
        dw = -du - dv
        u2, v2, w2 = clip_bary_interior(u + du, v + dv, w + dw, eps=eps)
        return bary_to_cart(u2, v2, w2)

    def anneal_single_restart(npts, rng_seed, init_mode="lattice"):
        rng = random.Random(rng_seed)

        # Initialization: dual strategies
        if init_mode == "poisson":
            pts = poisson_initial_points(npts, rng, eps=1e-6)
        else:
            pts = grid_initial_points(npts, rng, eps=1e-6)

        state = TriAreaState(pts)
        current_min, argmin = state.global_min()
        best_pts = [list(p) for p in state.points]
        best_min = current_min

        # Simulated annealing parameters
        iterations = 28000
        T0 = 0.02   # initial "temperature" in area units (normalized)
        alpha = 0.99935  # decay per iteration
        T = T0
        base_step = 0.065  # relative to triangle side
        eps_inside = 1e-6
        uniqueness_eps = 1e-7

        # Step adaptation
        window = 250
        acc_in_window = 0
        tried_in_window = 0

        # Helper to compute dynamic K (number of worst triangles to guide moves)
        def dynamic_k(iter_idx):
            frac = iter_idx / max(1, iterations - 1)
            if frac < 0.4:
                return 8
            elif frac < 0.8:
                return 5
            elif frac < 0.9:
                return 3
            else:
                return 1

        # Subgradient critical set computation (no longer used directly, but kept)
        def get_critical_triangles(cur_min):
            # Select triangles within a small tolerance of the min
            tol = max(1e-6, min(0.002, 0.03 * max(cur_min, 1e-9)))
            crit = []
            for ti, a in enumerate(state.areas):
                if a <= cur_min + tol:
                    crit.append(ti)
            return crit

        # Temperature-controlled soft-min multi-point subgradient step
        # Replaces the hard critical-set aggregation with a soft-min over a pool of worst triangles
        # MODIFICATION: Add short, projected backtracking line search over coordinated multi-point step.
        def subgradient_step(current_min_in, temp_scale, Tcur):
            # Determine pool size W based on current dynamic_k and cap
            K = dynamic_k(t)
            W = min(max(3 * K, 3), 24)  # ensure at least a few triangles
            worst = state.worst_k(W)
            if not worst:
                return False, current_min_in

            # Compute soft-min weights w_t ∝ exp(-α (A_t - A_min))
            # α schedule: α = α0 * (T0 / max(T, 1e-6))^β, capped
            alpha0 = 30.0
            beta_a = 0.5
            alpha_soft = alpha0 * ((T0 / max(Tcur, 1e-6)) ** beta_a)
            alpha_soft = min(alpha_soft, 150.0)

            A_min_pool = min(a for a, _ in worst)
            weights = []
            sum_w = 0.0
            for a, _ in worst:
                # Stable exponent using difference from A_min_pool
                w = math.exp(-alpha_soft * (a - A_min_pool))
                weights.append(w)
                sum_w += w
            if sum_w <= 0.0:
                # Fallback: uniform weights
                weights = [1.0 / len(worst)] * len(worst)
            else:
                weights = [w / sum_w for w in weights]

            # Aggregate weighted gradients per point over the pool
            g = [(0.0, 0.0) for _ in range(npts)]
            incident_weight = [0.0 for _ in range(npts)]
            involved = set()

            for (wt, (a, ti)) in zip(weights, worst):
                i, j, k = state.combos[ti]
                p1 = state.points[i]
                p2 = state.points[j]
                p3 = state.points[k]
                _, g1, g2, g3 = tri_area_signed_and_grads_norm(p1, p2, p3)

                # Accumulate weighted gradients
                gx, gy = g[i]
                g[i] = (gx + wt * g1[0], gy + wt * g1[1]); involved.add(i)
                gx, gy = g[j]
                g[j] = (gx + wt * g2[0], gy + wt * g2[1]); involved.add(j)
                gx, gy = g[k]
                g[k] = (gx + wt * g3[0], gy + wt * g3[1]); involved.add(k)

                # Track how much weight hits each point to mildly equalize step sizes
                incident_weight[i] += wt
                incident_weight[j] += wt
                incident_weight[k] += wt

            if not involved:
                return False, current_min_in

            # Weak repulsion among involved points to avoid tight clustering
            involved_list = list(involved)
            repel_R = 0.06  # radius
            rep_strength = 0.12
            for a_idx in involved_list:
                ax, ay = state.points[a_idx]
                rgx, rgy = 0.0, 0.0
                for b_idx in involved_list:
                    if b_idx == a_idx:
                        continue
                    bx, by = state.points[b_idx]
                    dx = ax - bx
                    dy = ay - by
                    d2 = dx * dx + dy * dy
                    if d2 <= 1e-16:
                        continue
                    d = math.sqrt(d2)
                    if d < repel_R:
                        # Linear falloff
                        w = (repel_R - d) / (repel_R * repel_R)
                        rgx += (dx / d) * w
                        rgy += (dy / d) * w
                if rgx != 0.0 or rgy != 0.0:
                    gx, gy = g[a_idx]
                    g[a_idx] = (gx + rep_strength * rgx, gy + rep_strength * rgy)

            # Per-point direction normalization and base step
            grad_step = base_step * (0.12 + 0.58 * temp_scale)
            barrier_thr = 0.03

            # Precompute per-point move vectors v_i from aggregated gradients
            v = {}
            for idx in involved_list:
                gx, gy = g[idx]
                glen = math.hypot(gx, gy)
                if glen < 1e-12:
                    continue
                ux, uy = gx / glen, gy / glen

                # Optional downscale by sqrt of incident soft weights to equalize influence
                incw = max(incident_weight[idx], 1e-12)
                per_point_scale = 1.0 / math.sqrt(incw)

                # Barrier scaling near boundary
                x0, y0 = state.points[idx]
                u_b, v_b, w_b = cart_to_bary(x0, y0)
                minb = max(0.0, min(u_b, v_b, w_b) - 1e-6)
                barrier_scale = 1.0
                if minb < barrier_thr:
                    barrier_scale = 0.5 + 0.5 * (minb / barrier_thr)

                step = grad_step * barrier_scale * per_point_scale
                v[idx] = (step * ux, step * uy)

            # If no valid direction vectors were formed, abort
            if not v:
                return False, current_min_in

            # Backtracking line search ladder scales
            scales = [1.0, 0.6, 0.36, 0.216]

            # Compute soft-min surrogate S over the pool (same triangles) with stability
            def compute_softmin_S():
                # Stable LogSumExp using A_min_pool
                Sexp = 0.0
                for _, ti in worst:
                    a_t = state.areas[ti]
                    Sexp += math.exp(-alpha_soft * (a_t - A_min_pool))
                # S = A_min_pool - (1/alpha) * log(Sexp)
                if Sexp <= 0.0:
                    # Degenerate; return min as fallback
                    return A_min_pool
                return A_min_pool - (1.0 / alpha_soft) * math.log(Sexp)

            S_old = compute_softmin_S()

            # Build affected triangle set for incremental recomputation
            # We'll re-evaluate by calling reeval_point for each moved idx (simple and safe)
            # and rely on state.areas in the worst pool.
            accepted = False
            cur_min = current_min_in

            best_candidate_S = -float("inf")
            best_candidate_scale = None

            # Save old positions for reversion
            old_positions = {idx: state.points[idx] for idx in involved_list}

            # Try line-search ladder: accept the first candidate that increases S
            for s in scales:
                # Apply scaled move for all involved points with projection inside
                for idx in involved_list:
                    vx, vy = v.get(idx, (0.0, 0.0))
                    ox, oy = old_positions[idx]
                    nx, ny = ensure_point_inside(ox + s * vx, oy + s * vy, eps=1e-6)
                    state.points[idx] = (nx, ny)

                # Uniqueness guard for all moved points simultaneously
                violated = False
                for idx in involved_list:
                    if min_distance_to_others(state.points, idx) < uniqueness_eps:
                        violated = True
                        break

                if violated:
                    # Revert to old positions and try next scale
                    for idx in involved_list:
                        state.points[idx] = old_positions[idx]
                    continue

                # Incrementally update areas for affected triangles
                for idx in involved_list:
                    state.reeval_point(idx)

                # Compute surrogate S_new on same worst pool
                S_new = compute_softmin_S()

                # Track best candidate for potential SA acceptance
                if S_new > best_candidate_S:
                    best_candidate_S = S_new
                    best_candidate_scale = s

                # Accept if S improved
                if S_new > S_old + 1e-12:
                    # Update current_min using actual min area after accepted move
                    new_min, _ = state.global_min()
                    cur_min = new_min
                    accepted = True
                    break  # accept first improvement
                else:
                    # Revert points and areas to old before trying next scale
                    for idx in involved_list:
                        state.points[idx] = old_positions[idx]
                    for idx in involved_list:
                        state.reeval_point(idx)

            if not accepted:
                # If no scale improved S, consider SA acceptance of the best candidate
                if best_candidate_scale is not None and best_candidate_S > -float("inf"):
                    # Temperature for soft-min objective acceptance
                    T_soft = max(1e-6, 0.6 * Tcur)
                    deltaS = best_candidate_S - S_old
                    prob = math.exp(deltaS / T_soft) if T_soft > 0 else 0.0
                    if rng.random() < prob:
                        s = best_candidate_scale
                        # Apply best candidate again and accept
                        for idx in involved_list:
                            vx, vy = v.get(idx, (0.0, 0.0))
                            ox, oy = old_positions[idx]
                            nx, ny = ensure_point_inside(ox + s * vx, oy + s * vy, eps=1e-6)
                            state.points[idx] = (nx, ny)
                        # Uniqueness guard again (should hold as before)
                        # Recompute areas for consistency
                        for idx in involved_list:
                            state.reeval_point(idx)
                        new_min, _ = state.global_min()
                        cur_min = new_min
                        accepted = True
                    else:
                        # Explicitly revert to old positions to be safe
                        for idx in involved_list:
                            state.points[idx] = old_positions[idx]
                        for idx in involved_list:
                            state.reeval_point(idx)
                else:
                    # No candidate; just ensure we are at old positions
                    for idx in involved_list:
                        state.points[idx] = old_positions[idx]
                    for idx in involved_list:
                        state.reeval_point(idx)

            return accepted, cur_min

        for t in range(iterations):
            # Cooling and step scaling
            T *= alpha
            temp_scale = max(0.1, min(1.0, T / T0))
            step_size = base_step * (0.20 + 0.80 * temp_scale)

            k_small = dynamic_k(t)
            worst_k = state.worst_k(k_small)

            # Move family scheduling: targeted vs subgradient vs random
            frac = t / max(1, iterations - 1)
            p_subgrad = 0.15 if frac < 0.3 else (0.45 if frac < 0.75 else 0.55)
            p_targeted = 0.70 if frac < 0.5 else (0.55 if frac < 0.85 else 0.40)
            # Normalize to leave some room for random
            p_subgrad = max(0.0, min(0.8, p_subgrad))
            p_targeted = max(0.0, min(0.9, p_targeted))
            p_random = 1.0 - max(0.0, min(1.0, p_subgrad + p_targeted))
            rpick = rng.random()

            accepted_this_iter = False

            if rpick < p_targeted and worst_k:
                # Targeted move: select a random worst triangle among K and a random vertex
                _, tri_idx = worst_k[rng.randrange(len(worst_k))]
                tri = state.combos[tri_idx]
                idx_choice = tri[rng.randrange(3)]

                # Propose a directed move
                proposal = make_move_on_worst(state.points, tri, idx_choice, step_size, rng,
                                              eps_inside=eps_inside,
                                              t_fraction=0.25, noise_fraction=0.10)
                if proposal is not None:
                    newx, newy = proposal
                    oldx, oldy = state.points[idx_choice]
                    state.points[idx_choice] = (newx, newy)
                    # Uniqueness guard
                    if min_distance_to_others(state.points, idx_choice) < uniqueness_eps:
                        # Revert
                        state.points[idx_choice] = (oldx, oldy)
                    else:
                        # Update areas incrementally
                        state.reeval_point(idx_choice)
                        new_min, _ = state.global_min()
                        # Acceptance
                        accept = False
                        if new_min >= current_min:
                            accept = True
                        else:
                            delta = new_min - current_min
                            prob = math.exp(delta / max(T, 1e-12)) if T > 0 else 0.0
                            if rng.random() < prob:
                                accept = True
                        if accept:
                            current_min = new_min
                            accepted_this_iter = True
                            # Optional paired tweak with small probability to relax shared edges
                            if rng.random() < 0.10 and k_small <= 5:
                                # Pick one of base vertices and apply tiny tangential tweak
                                others = [tri[0], tri[1], tri[2]]
                                others.remove(idx_choice)
                                base_choice = others[rng.randrange(2)]
                                # Use a very small step with mostly tangential component
                                secondary = make_move_on_worst(
                                    state.points, tri, base_choice,
                                    step_size * 0.35, rng, eps_inside=eps_inside,
                                    t_fraction=0.80, noise_fraction=0.05
                                )
                                if secondary is not None:
                                    bx, by = state.points[base_choice]
                                    nx, ny = secondary
                                    state.points[base_choice] = (nx, ny)
                                    if min_distance_to_others(state.points, base_choice) < uniqueness_eps:
                                        state.points[base_choice] = (bx, by)
                                    else:
                                        state.reeval_point(base_choice)
                                        new2_min, _ = state.global_min()
                                        if new2_min >= current_min:
                                            current_min = new2_min
                                            accepted_this_iter = True
                                        else:
                                            # SA acceptance for secondary (weakened)
                                            delta2 = new2_min - current_min
                                            prob2 = math.exp(delta2 / max(T * 0.5, 1e-12)) if T > 0 else 0.0
                                            if rng.random() < prob2:
                                                current_min = new2_min
                                                accepted_this_iter = True
                                            else:
                                                # revert secondary
                                                state.points[base_choice] = (bx, by)
                                                state.reeval_point(base_choice)
                        else:
                            # Revert and restore areas by re-evaluating
                            state.points[idx_choice] = (oldx, oldy)
                            state.reeval_point(idx_choice)

            elif rpick < p_targeted + p_subgrad:
                # Soft-min multi-point subgradient step (coordinated small moves)
                did, new_min = subgradient_step(current_min, temp_scale, T)
                if did:
                    current_min = new_min
                    accepted_this_iter = True
            else:
                # Random exploration in barycentric space
                idx = rng.randrange(npts)
                ox, oy = state.points[idx]
                rx, ry = random_barycentric_perturb(state.points, idx, radius=0.025 * (temp_scale ** 0.7), rng=rng, eps=eps_inside)
                rx, ry = ensure_point_inside(rx, ry, eps=eps_inside)
                state.points[idx] = (rx, ry)
                if min_distance_to_others(state.points, idx) < uniqueness_eps:
                    state.points[idx] = (ox, oy)
                else:
                    state.reeval_point(idx)
                    new_min, _ = state.global_min()
                    if new_min >= current_min:
                        current_min = new_min
                        accepted_this_iter = True
                    else:
                        delta = new_min - current_min
                        prob = math.exp(delta / max(T, 1e-12)) if T > 0 else 0.0
                        if rng.random() < prob:
                            current_min = new_min
                            accepted_this_iter = True
                        else:
                            state.points[idx] = (ox, oy)
                            state.reeval_point(idx)

            # Update best-so-far
            if current_min > best_min:
                best_min = current_min
                best_pts = [list(p) for p in state.points]

            # Step-size adaptation
            tried_in_window += 1
            if accepted_this_iter:
                acc_in_window += 1
            if tried_in_window >= window:
                acc_ratio = acc_in_window / max(1, tried_in_window)
                if acc_ratio > 0.60:
                    base_step *= 1.12
                elif acc_ratio < 0.25:
                    base_step *= 0.86
                # Clamp base_step
                base_step = max(0.008, min(0.12, base_step))
                # Reset window counters
                acc_in_window = 0
                tried_in_window = 0

            # Additional periodic random micro-perturbation to escape traps
            if (t + 1) % 35 == 0:
                idx = rng.randrange(npts)
                ox, oy = state.points[idx]
                rx, ry = random_barycentric_perturb(state.points, idx, radius=0.015 * (temp_scale ** 0.5), rng=rng, eps=eps_inside)
                rx, ry = ensure_point_inside(rx, ry, eps=eps_inside)
                state.points[idx] = (rx, ry)
                if min_distance_to_others(state.points, idx) < 1e-7:
                    state.points[idx] = (ox, oy)
                    continue
                state.reeval_point(idx)
                new_min, _ = state.global_min()
                if new_min >= current_min:
                    current_min = new_min
                    if current_min > best_min:
                        best_min = current_min
                        best_pts = [list(p) for p in state.points]
                else:
                    # Weak SA acceptance
                    delta = new_min - current_min
                    prob = math.exp(delta / max(T * 0.5, 1e-12)) if T > 0 else 0.0
                    if rng.random() < prob:
                        current_min = new_min
                        if current_min > best_min:
                            best_min = current_min
                            best_pts = [list(p) for p in state.points]
                    else:
                        state.points[idx] = (ox, oy)
                        state.reeval_point(idx)

        # Post-optimization polish: deterministic pattern search and micro-moves
        state.points = [tuple(p) for p in best_pts]
        state.recompute_all()
        current_min, _ = state.global_min()

        # Pattern search: try a set of directions with decreasing step sizes
        directions = []
        for k in range(12):
            ang = 2.0 * math.pi * (k / 12.0)
            directions.append((math.cos(ang), math.sin(ang)))

        step = 0.025
        for outer in range(10):
            improved_any = False
            for idx in range(npts):
                for d in directions:
                    dx, dy = d[0] * step, d[1] * step
                    ox, oy = state.points[idx]
                    nx, ny = ensure_point_inside(ox + dx, oy + dy, eps=1e-6)
                    state.points[idx] = (nx, ny)
                    if min_distance_to_others(state.points, idx) < 1e-7:
                        state.points[idx] = (ox, oy)
                        continue
                    state.reeval_point(idx)
                    new_min, _ = state.global_min()
                    if new_min > current_min + 1e-12:
                        current_min = new_min
                        improved_any = True
                        if current_min > best_min:
                            best_min = current_min
                            best_pts = [list(p) for p in state.points]
                    else:
                        # revert
                        state.points[idx] = (ox, oy)
                        state.reeval_point(idx)
            step *= 0.6
            if not improved_any and step < 0.004:
                break

        # Worst-triangle micro-moves: push vertices along the normal only
        state.points = [tuple(p) for p in best_pts]
        state.recompute_all()
        current_min, _ = state.global_min()
        micro_step = 0.015
        for it in range(320):
            # Reduce step slowly
            micro_step *= 0.98
            # Determine current worst triangle
            amin, tri_idx = state.global_min()
            tri = state.combos[tri_idx]
            # Try pure normal moves for each vertex; pick best
            best_improvement = 0.0
            best_mod = None
            for idx_choice in tri:
                # Use move generator but with no noise, minimal tangential
                proposal = make_move_on_worst(state.points, tri, idx_choice, micro_step,
                                              rng=random.Random(1234 + it + idx_choice),
                                              eps_inside=1e-6,
                                              t_fraction=0.0, noise_fraction=0.0)
                if proposal is None:
                    continue
                nx, ny = proposal
                ox, oy = state.points[idx_choice]
                state.points[idx_choice] = (nx, ny)
                if min_distance_to_others(state.points, idx_choice) < 1e-7:
                    state.points[idx_choice] = (ox, oy)
                    continue
                state.reeval_point(idx_choice)
                new_min, _ = state.global_min()
                if new_min - current_min > best_improvement + 1e-15:
                    best_improvement = new_min - current_min
                    best_mod = (idx_choice, (nx, ny))
                # revert local change after evaluation
                state.points[idx_choice] = (ox, oy)
                state.reeval_point(idx_choice)
            if best_mod is not None and best_improvement > 0.0:
                idx_choice, (nx, ny) = best_mod
                state.points[idx_choice] = (nx, ny)
                state.reeval_point(idx_choice)
                current_min, _ = state.global_min()
                if current_min > best_min:
                    best_min = current_min
                    best_pts = [list(p) for p in state.points]
            else:
                if micro_step < 0.003:
                    break

        return best_pts, best_min

    def find_best_placement(npts):
        # Multiple restarts to escape local minima
        base_seed = 20231111
        restarts = 14
        best_overall_pts = None
        best_overall_min = -1.0
        for r in range(restarts):
            seed = base_seed + r * 997
            mode = "lattice" if (r % 2 == 0) else "poisson"
            pts, min_area = anneal_single_restart(npts, seed, init_mode=mode)
            if min_area > best_overall_min:
                best_overall_min = min_area
                best_overall_pts = pts
        return best_overall_pts, best_overall_min

    points, minimum_area = find_best_placement(11)
    # Ensure data types are basic lists for JSON compatibility
    points_out = [list(p) for p in points]
    return points_out, minimum_area
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))
```

```python
#!/usr/bin/env python3
"""Heilbronn problem solver for 11 points in an equilateral triangle.

We maximize the minimum triangle area formed by any three of the points.
The algorithm combines worst-triangle-guided local moves, simulated annealing,
critical-triple subgradient ascent, and multiple restarts, while strictly
keeping all points inside the triangle via barycentric coordinates.

Triangle vertices:
A = (0, 0)
B = (1, 0)
C = (0.5, sqrt(3)/2)
"""

import json
import itertools
import math
import random
import heapq


# EVOLVE_START
def run_search_point(n=11):
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")

    # Geometry constants for the reference equilateral triangle
    SQRT3 = math.sqrt(3.0)
    TRI_HEIGHT = SQRT3 * 0.5
    AREA_TRI = SQRT3 / 4.0  # area of the big triangle

    # Vertices of the equilateral triangle
    A = (0.0, 0.0)
    B = (1.0, 0.0)
    C = (0.5, TRI_HEIGHT)

    # Index combinations for triangles with n points
    def all_triangle_indices(npts):
        return list(itertools.combinations(range(npts), 3))

    # Barycentric <-> Cartesian conversions.
    # Barycentric (u, v, w) requires u + v + w = 1 and u, v, w > 0 for interior.
    def bary_to_cart(u, v, w):
        # P = u*A + v*B + w*C; with A=(0,0), B=(1,0), C=(0.5,h)
        x = v + 0.5 * w
        y = TRI_HEIGHT * w
        return x, y

    def cart_to_bary(x, y):
        # From y: w = 2*y/sqrt(3)
        w = (2.0 / SQRT3) * y
        # From x: x = v + 0.5*w -> v = x - 0.5*w
        v = x - 0.5 * w
        # u = 1 - v - w
        u = 1.0 - v - w
        return u, v, w

    def clip_bary_interior(u, v, w, eps=1e-6):
        # Project onto barycentric simplex with lower bound eps for strict interior.
        # The map: clamp to >= eps, then renormalize to sum 1.
        u = max(u, eps)
        v = max(v, eps)
        w = max(w, eps)
        s = u + v + w
        if s <= 0:
            u = v = w = 1.0 / 3.0
            u = max(u, eps)
            v = max(v, eps)
            w = max(w, eps)
            s = u + v + w
        u /= s
        v /= s
        w /= s
        return u, v, w

    def ensure_point_inside(x, y, eps=1e-6):
        u, v, w = cart_to_bary(x, y)
        u, v, w = clip_bary_interior(u, v, w, eps=eps)
        return bary_to_cart(u, v, w)

    def cross(ax, ay, bx, by):
        return ax * by - ay * bx

    def tri_area_norm(p1, p2, p3):
        # area normalized by AREA_TRI
        x1, y1 = p1
        x2, y2 = p2
        x3, y3 = p3
        area2 = abs((x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1))  # 2*area
        area = 0.5 * area2
        return area / AREA_TRI

    def tri_area_signed_and_grads_norm(p1, p2, p3):
        # Signed normalized area and gradients of absolute area.
        # Signed area As = 0.5 * ((x2-x1)(y3-y1) - (y2-y1)(x3-x1))
        # Grad of signed area w.r.t p1: 0.5 * (y2 - y3, x3 - x2)
        # w.r.t p2: 0.5 * (y3 - y1, x1 - x3)
        # w.r.t p3: 0.5 * (y1 - y2, x2 - x1)
        x1, y1 = p1
        x2, y2 = p2
        x3, y3 = p3
        f = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)
        As = 0.5 * f
        As_norm = As / AREA_TRI
        # sign for absolute area subgradient
        sgn = 1.0 if As >= 0.0 else -1.0
        # grads of absolute normalized area
        scale = 0.5 * sgn / AREA_TRI
        g1x = scale * (y2 - y3)
        g1y = scale * (x3 - x2)
        g2x = scale * (y3 - y1)
        g2y = scale * (x1 - x3)
        g3x = scale * (y1 - y2)
        g3y = scale * (x2 - x1)
        return As_norm, (g1x, g1y), (g2x, g2y), (g3x, g3y)

    def min_distance_to_others(points, idx):
        xi, yi = points[idx]
        mind2 = float("inf")
        for j, (xj, yj) in enumerate(points):
            if j == idx:
                continue
            dx = xi - xj
            dy = yi - yj
            d2 = dx * dx + dy * dy
            if d2 < mind2:
                mind2 = d2
        return math.sqrt(mind2) if mind2 < float("inf") else float("inf")

    class TriAreaState:
        """Maintain triangle areas incrementally for a set of points."""
        def __init__(self, points):
            self.n = len(points)
            self.points = [tuple(p) for p in points]
            self.combos = all_triangle_indices(self.n)
            self.m = len(self.combos)
            # For each point index, list of area indices that include it
            self.combos_by_point = [[] for _ in range(self.n)]
            for ti, (i, j, k) in enumerate(self.combos):
                self.combos_by_point[i].append(ti)
                self.combos_by_point[j].append(ti)
                self.combos_by_point[k].append(ti)
            self.areas = [0.0] * self.m
            self.recompute_all()

        def recompute_all(self):
            for ti, (i, j, k) in enumerate(self.combos):
                self.areas[ti] = tri_area_norm(self.points[i], self.points[j], self.points[k])

        def reeval_point(self, idx):
            # Recompute areas for all triangles including point idx
            for ti in self.combos_by_point[idx]:
                i, j, k = self.combos[ti]
                self.areas[ti] = tri_area_norm(self.points[i], self.points[j], self.points[k])

        def global_min(self):
            # Return min area and its triangle index
            amin = float("inf")
            arg = -1
            for ti, a in enumerate(self.areas):
                if a < amin:
                    amin = a
                    arg = ti
            return amin, arg

        def worst_k(self, k):
            # Return list of (area, triangle_index), smallest k
            k = min(k, self.m)
            idxs = heapq.nsmallest(k, range(self.m), key=lambda ti: self.areas[ti])
            return [(self.areas[ti], ti) for ti in idxs]

    # Initialization strategies

    def farthest_point_subset(candidates, k, rng):
        # Select k points from candidates using farthest-point sampling to maximize spread
        if k >= len(candidates):
            return list(candidates)
        chosen = []
        remaining = list(candidates)
        # Start from the point farthest from triangle centroid for variety
        cx, cy = 0.5, TRI_HEIGHT / 3.0 * 2.0
        best_idx = 0
        best_d2 = -1.0
        for idx, (px, py) in enumerate(remaining):
            dx = px - cx
            dy = py - cy
            d2 = dx * dx + dy * dy
            if d2 > best_d2:
                best_d2 = d2
                best_idx = idx
        chosen.append(remaining.pop(best_idx))
        while len(chosen) < k and remaining:
            best_idx = None
            best_dist = -1.0
            for idx, p in enumerate(remaining):
                px, py = p
                md2 = float("inf")
                for (cx, cy) in chosen:
                    dx = px - cx
                    dy = py - cy
                    d2 = dx * dx + dy * dy
                    if d2 < md2:
                        md2 = d2
                if md2 > best_dist:
                    best_dist = md2
                    best_idx = idx
            if best_idx is None:
                break
            chosen.append(remaining.pop(best_idx))
        return chosen

    def grid_initial_points(npts, rng, eps=1e-6):
        # Build an interior triangular lattice using integer barycentric coords
        # with i+j+k = M and i,j,k >= 1. For M=7, count is C(6,2)=15 interior points.
        M = 7
        lattice = []
        for i in range(1, M):
            for j in range(1, M - i):
                k = M - i - j
                if k < 1:
                    continue
                u = i / M
                v = j / M
                w = k / M
                # small jitter in barycentric space to avoid degeneracy
                jb = 0.007
                du = (rng.random() - 0.5) * jb
                dv = (rng.random() - 0.5) * jb
                dw = -du - dv
                u2, v2, w2 = clip_bary_interior(u + du, v + dv, w + dw, eps=eps)
                x, y = bary_to_cart(u2, v2, w2)
                lattice.append((x, y))
        # Select npts via farthest-point sampling
        pts = farthest_point_subset(lattice, npts, rng)
        # If not enough, add random points inside
        while len(pts) < npts:
            # Random barycentric strictly inside via normalized positive samples
            a = rng.random() + 1e-6
            b = rng.random() + 1e-6
            c = rng.random() + 1e-6
            s = a + b + c
            u, v, w = a / s, b / s, c / s
            u, v, w = clip_bary_interior(u, v, w, eps=eps)
            pts.append(bary_to_cart(u, v, w))
        # Ensure strictly inside
        pts2 = [ensure_point_inside(x, y, eps=eps) for (x, y) in pts]
        return pts2

    def poisson_initial_points(npts, rng, eps=1e-6):
        # Greedy farthest-point (Poisson-like) placement with many random candidates per point
        pts = []
        # Start from centroid-ish point
        u, v, w = 0.34, 0.33, 0.33
        x, y = bary_to_cart(u, v, w)
        pts.append(ensure_point_inside(x, y, eps=eps))
        candidates_per_step = 600
        jitter_bary = 0.01
        while len(pts) < npts:
            best = None
            best_md2 = -1.0
            for _ in range(candidates_per_step):
                # Random interior barycentric
                a = rng.random() + 1e-6
                b = rng.random() + 1e-6
                c = rng.random() + 1e-6
                s = a + b + c
                u, v, w = a / s, b / s, c / s
                # Tiny barycentric jitter
                du = (rng.random() - 0.5) * 2.0 * jitter_bary
                dv = (rng.random() - 0.5) * 2.0 * jitter_bary
                dw = -du - dv
                u2, v2, w2 = clip_bary_interior(u + du, v + dv, w + dw, eps=eps)
                cx, cy = bary_to_cart(u2, v2, w2)
                # Compute min distance squared to existing pts
                md2 = float("inf")
                for (px, py) in pts:
                    dx = cx - px
                    dy = cy - py
                    d2 = dx * dx + dy * dy
                    if d2 < md2:
                        md2 = d2
                if md2 > best_md2:
                    best_md2 = md2
                    best = (cx, cy)
            if best is None:
                # Fallback: random interior
                u, v, w = clip_bary_interior(rng.random(), rng.random(), rng.random(), eps=eps)
                best = bary_to_cart(u, v, w)
            pts.append(ensure_point_inside(best[0], best[1], eps=eps))
        return pts

    # Move proposals

    def make_move_on_worst(points, combo, idx_to_move, step_size, rng, eps_inside=1e-6,
                           t_fraction=0.25, noise_fraction=0.10):
        # Move point idx_to_move to increase area of the given triangle combo
        i, j, k = combo
        others = [i, j, k]
        others.remove(idx_to_move)
        jidx, kidx = others[0], others[1]
        p = points[idx_to_move]
        pj = points[jidx]
        pk = points[kidx]

        # Base vector b = pk - pj
        bx = pk[0] - pj[0]
        by = pk[1] - pj[1]
        # Gradient direction ~ sign * (-by, bx)
        cx = p[0] - pj[0]
        cy = p[1] - pj[1]
        cross_val = cross(bx, by, cx, cy)
        sign = 1.0 if cross_val >= 0.0 else -1.0

        # Normal unit
        nx = -by
        ny = bx
        nlen = math.hypot(nx, ny)
        if nlen == 0.0:
            return None  # degenerate base; skip
        nx /= nlen
        ny /= nlen
        nx *= sign
        ny *= sign

        # Tangential unit along base
        blen = math.hypot(bx, by)
        if blen == 0.0:
            return None
        tx = bx / blen
        ty = by / blen

        # Proposed displacement: primarily along normal, a little along tangent + noise
        lam = 0.5 + 0.5 * rng.random()
        sx = step_size * lam

        t_sign = -1.0 if rng.random() < 0.5 else 1.0
        ang = 2.0 * math.pi * rng.random()
        ux = math.cos(ang)
        uy = math.sin(ang)

        dx = sx * (nx + t_fraction * t_sign * tx + noise_fraction * ux)
        dy = sx * (ny + t_fraction * t_sign * ty + noise_fraction * uy)

        newx = p[0] + dx
        newy = p[1] + dy
        newx, newy = ensure_point_inside(newx, newy, eps=eps_inside)

        return newx, newy

    def random_barycentric_perturb(points, idx, radius, rng, eps=1e-6):
        # Small random perturbation in barycentric space around current point
        x, y = points[idx]
        u, v, w = cart_to_bary(x, y)
        du = (rng.random() - 0.5) * 2.0 * radius
        dv = (rng.random() - 0.5) * 2.0 * radius
        dw = -du - dv
        u2, v2, w2 = clip_bary_interior(u + du, v + dv, w + dw, eps=eps)
        return bary_to_cart(u2, v2, w2)

    def anneal_single_restart(npts, rng_seed, init_mode="lattice"):
        rng = random.Random(rng_seed)

        # Initialization: dual strategies
        if init_mode == "poisson":
            pts = poisson_initial_points(npts, rng, eps=1e-6)
        else:
            pts = grid_initial_points(npts, rng, eps=1e-6)

        state = TriAreaState(pts)
        current_min, argmin = state.global_min()
        best_pts = [list(p) for p in state.points]
        best_min = current_min

        # Simulated annealing parameters
        iterations = 28000
        T0 = 0.02   # initial "temperature" in area units (normalized)
        alpha = 0.99935  # decay per iteration
        T = T0
        base_step = 0.065  # relative to triangle side
        eps_inside = 1e-6
        uniqueness_eps = 1e-7

        # Step adaptation
        window = 250
        acc_in_window = 0
        tried_in_window = 0

        # Helper to compute dynamic K (number of worst triangles to guide moves)
        def dynamic_k(iter_idx):
            frac = iter_idx / max(1, iterations - 1)
            if frac < 0.4:
                return 8
            elif frac < 0.8:
                return 5
            elif frac < 0.9:
                return 3
            else:
                return 1

        # Subgradient critical set computation (no longer used directly, but kept)
        def get_critical_triangles(cur_min):
            # Select triangles within a small tolerance of the min
            tol = max(1e-6, min(0.002, 0.03 * max(cur_min, 1e-9)))
            crit = []
            for ti, a in enumerate(state.areas):
                if a <= cur_min + tol:
                    crit.append(ti)
            return crit

        # Temperature-controlled soft-min multi-point subgradient step
        # Replaces the hard critical-set aggregation with a soft-min over a pool of worst triangles
        # MODIFICATION: Add short, projected backtracking line search over coordinated multi-point step.
        def subgradient_step(current_min_in, temp_scale, Tcur):
            # Determine pool size W based on current dynamic_k and cap
            K = dynamic_k(t)
            W = min(max(3 * K, 3), 24)  # ensure at least a few triangles
            worst = state.worst_k(W)
            if not worst:
                return False, current_min_in

            # Compute soft-min weights w_t ∝ exp(-α (A_t - A_min))
            # α schedule: α = α0 * (T0 / max(T, 1e-6))^β, capped
            alpha0 = 30.0
            beta_a = 0.5
            alpha_soft = alpha0 * ((T0 / max(Tcur, 1e-6)) ** beta_a)
            alpha_soft = min(alpha_soft, 150.0)

            A_min_pool = min(a for a, _ in worst)
            weights = []
            sum_w = 0.0
            for a, _ in worst:
                # Stable exponent using difference from A_min_pool
                w = math.exp(-alpha_soft * (a - A_min_pool))
                weights.append(w)
                sum_w += w
            if sum_w <= 0.0:
                # Fallback: uniform weights
                weights = [1.0 / len(worst)] * len(worst)
            else:
                weights = [w / sum_w for w in weights]

            # Aggregate weighted gradients per point over the pool
            g = [(0.0, 0.0) for _ in range(npts)]
            incident_weight = [0.0 for _ in range(npts)]
            involved = set()

            for (wt, (a, ti)) in zip(weights, worst):
                i, j, k = state.combos[ti]
                p1 = state.points[i]
                p2 = state.points[j]
                p3 = state.points[k]
                _, g1, g2, g3 = tri_area_signed_and_grads_norm(p1, p2, p3)

                # Accumulate weighted gradients
                gx, gy = g[i]
                g[i] = (gx + wt * g1[0], gy + wt * g1[1]); involved.add(i)
                gx, gy = g[j]
                g[j] = (gx + wt * g2[0], gy + wt * g2[1]); involved.add(j)
                gx, gy = g[k]
                g[k] = (gx + wt * g3[0], gy + wt * g3[1]); involved.add(k)

                # Track how much weight hits each point to mildly equalize step sizes
                incident_weight[i] += wt
                incident_weight[j] += wt
                incident_weight[k] += wt

            if not involved:
                return False, current_min_in

            # Weak repulsion among involved points to avoid tight clustering
            involved_list = list(involved)
            repel_R = 0.06  # radius
            rep_strength = 0.12
            for a_idx in involved_list:
                ax, ay = state.points[a_idx]
                rgx, rgy = 0.0, 0.0
                for b_idx in involved_list:
                    if b_idx == a_idx:
                        continue
                    bx, by = state.points[b_idx]
                    dx = ax - bx
                    dy = ay - by
                    d2 = dx * dx + dy * dy
                    if d2 <= 1e-16:
                        continue
                    d = math.sqrt(d2)
                    if d < repel_R:
                        # Linear falloff
                        w = (repel_R - d) / (repel_R * repel_R)
                        rgx += (dx / d) * w
                        rgy += (dy / d) * w
                if rgx != 0.0 or rgy != 0.0:
                    gx, gy = g[a_idx]
                    g[a_idx] = (gx + rep_strength * rgx, gy + rep_strength * rgy)

            # Per-point direction normalization and base step
            grad_step = base_step * (0.12 + 0.58 * temp_scale)
            barrier_thr = 0.03

            # Precompute per-point move vectors v_i from aggregated gradients
            v = {}
            for idx in involved_list:
                gx, gy = g[idx]
                glen = math.hypot(gx, gy)
                if glen < 1e-12:
                    continue
                ux, uy = gx / glen, gy / glen

                # Optional downscale by sqrt of incident soft weights to equalize influence
                incw = max(incident_weight[idx], 1e-12)
                per_point_scale = 1.0 / math.sqrt(incw)

                # Barrier scaling near boundary
                x0, y0 = state.points[idx]
                u_b, v_b, w_b = cart_to_bary(x0, y0)
                minb = max(0.0, min(u_b, v_b, w_b) - 1e-6)
                barrier_scale = 1.0
                if minb < barrier_thr:
                    barrier_scale = 0.5 + 0.5 * (minb / barrier_thr)

                step = grad_step * barrier_scale * per_point_scale
                v[idx] = (step * ux, step * uy)

            # If no valid direction vectors were formed, abort
            if not v:
                return False, current_min_in

            # Backtracking line search ladder scales
            scales = [1.0, 0.6, 0.36, 0.216]

            # Compute soft-min surrogate S over the pool (same triangles) with stability
            def compute_softmin_S():
                # Stable LogSumExp using A_min_pool
                Sexp = 0.0
                for _, ti in worst:
                    a_t = state.areas[ti]
                    Sexp += math.exp(-alpha_soft * (a_t - A_min_pool))
                # S = A_min_pool - (1/alpha) * log(Sexp)
                if Sexp <= 0.0:
                    # Degenerate; return min as fallback
                    return A_min_pool
                return A_min_pool - (1.0 / alpha_soft) * math.log(Sexp)

            S_old = compute_softmin_S()

            # Build affected triangle set for incremental recomputation
            # We'll re-evaluate by calling reeval_point for each moved idx (simple and safe)
            # and rely on state.areas in the worst pool.
            accepted = False
            cur_min = current_min_in

            best_candidate_S = -float("inf")
            best_candidate_scale = None

            # Save old positions for reversion
            old_positions = {idx: state.points[idx] for idx in involved_list}

            # Try line-search ladder: accept the first candidate that increases S
            for s in scales:
                # Apply scaled move for all involved points with projection inside
                for idx in involved_list:
                    vx, vy = v.get(idx, (0.0, 0.0))
                    ox, oy = old_positions[idx]
                    nx, ny = ensure_point_inside(ox + s * vx, oy + s * vy, eps=1e-6)
                    state.points[idx] = (nx, ny)

                # Uniqueness guard for all moved points simultaneously
                violated = False
                for idx in involved_list:
                    if min_distance_to_others(state.points, idx) < uniqueness_eps:
                        violated = True
                        break

                if violated:
                    # Revert to old positions and try next scale
                    for idx in involved_list:
                        state.points[idx] = old_positions[idx]
                    continue

                # Incrementally update areas for affected triangles
                for idx in involved_list:
                    state.reeval_point(idx)

                # Compute surrogate S_new on same worst pool
                S_new = compute_softmin_S()

                # Track best candidate for potential SA acceptance
                if S_new > best_candidate_S:
                    best_candidate_S = S_new
                    best_candidate_scale = s

                # Accept if S improved
                if S_new > S_old + 1e-12:
                    # Update current_min using actual min area after accepted move
                    new_min, _ = state.global_min()
                    cur_min = new_min
                    accepted = True
                    break  # accept first improvement
                else:
                    # Revert points and areas to old before trying next scale
                    for idx in involved_list:
                        state.points[idx] = old_positions[idx]
                    for idx in involved_list:
                        state.reeval_point(idx)

            if not accepted:
                # If no scale improved S, consider SA acceptance of the best candidate
                if best_candidate_scale is not None and best_candidate_S > -float("inf"):
                    # Temperature for soft-min objective acceptance
                    T_soft = max(1e-6, 0.6 * Tcur)
                    deltaS = best_candidate_S - S_old
                    prob = math.exp(deltaS / T_soft) if T_soft > 0 else 0.0
                    if rng.random() < prob:
                        s = best_candidate_scale
                        # Apply best candidate again and accept
                        for idx in involved_list:
                            vx, vy = v.get(idx, (0.0, 0.0))
                            ox, oy = old_positions[idx]
                            nx, ny = ensure_point_inside(ox + s * vx, oy + s * vy, eps=1e-6)
                            state.points[idx] = (nx, ny)
                        # Uniqueness guard again (should hold as before)
                        # Recompute areas for consistency
                        for idx in involved_list:
                            state.reeval_point(idx)
                        new_min, _ = state.global_min()
                        cur_min = new_min
                        accepted = True
                    else:
                        # Explicitly revert to old positions to be safe
                        for idx in involved_list:
                            state.points[idx] = old_positions[idx]
                        for idx in involved_list:
                            state.reeval_point(idx)
                else:
                    # No candidate; just ensure we are at old positions
                    for idx in involved_list:
                        state.points[idx] = old_positions[idx]
                    for idx in involved_list:
                        state.reeval_point(idx)

            return accepted, cur_min

        for t in range(iterations):
            # Cooling and step scaling
            T *= alpha
            temp_scale = max(0.1, min(1.0, T / T0))
            step_size = base_step * (0.20 + 0.80 * temp_scale)

            k_small = dynamic_k(t)
            worst_k = state.worst_k(k_small)

            # Move family scheduling: targeted vs subgradient vs random
            frac = t / max(1, iterations - 1)
            p_subgrad = 0.15 if frac < 0.3 else (0.45 if frac < 0.75 else 0.55)
            p_targeted = 0.70 if frac < 0.5 else (0.55 if frac < 0.85 else 0.40)
            # Normalize to leave some room for random
            p_subgrad = max(0.0, min(0.8, p_subgrad))
            p_targeted = max(0.0, min(0.9, p_targeted))
            p_random = 1.0 - max(0.0, min(1.0, p_subgrad + p_targeted))
            rpick = rng.random()

            accepted_this_iter = False

            if rpick < p_targeted and worst_k:
                # Targeted move: select a random worst triangle among K and a random vertex
                _, tri_idx = worst_k[rng.randrange(len(worst_k))]
                tri = state.combos[tri_idx]
                idx_choice = tri[rng.randrange(3)]

                # Propose a directed move
                proposal = make_move_on_worst(state.points, tri, idx_choice, step_size, rng,
                                              eps_inside=eps_inside,
                                              t_fraction=0.25, noise_fraction=0.10)
                if proposal is not None:
                    newx, newy = proposal
                    oldx, oldy = state.points[idx_choice]
                    state.points[idx_choice] = (newx, newy)
                    # Uniqueness guard
                    if min_distance_to_others(state.points, idx_choice) < uniqueness_eps:
                        # Revert
                        state.points[idx_choice] = (oldx, oldy)
                    else:
                        # Update areas incrementally
                        state.reeval_point(idx_choice)
                        new_min, _ = state.global_min()
                        # Acceptance
                        accept = False
                        if new_min >= current_min:
                            accept = True
                        else:
                            delta = new_min - current_min
                            prob = math.exp(delta / max(T, 1e-12)) if T > 0 else 0.0
                            if rng.random() < prob:
                                accept = True
                        if accept:
                            current_min = new_min
                            accepted_this_iter = True
                            # Optional paired tweak with small probability to relax shared edges
                            if rng.random() < 0.10 and k_small <= 5:
                                # Pick one of base vertices and apply tiny tangential tweak
                                others = [tri[0], tri[1], tri[2]]
                                others.remove(idx_choice)
                                base_choice = others[rng.randrange(2)]
                                # Use a very small step with mostly tangential component
                                secondary = make_move_on_worst(
                                    state.points, tri, base_choice,
                                    step_size * 0.35, rng, eps_inside=eps_inside,
                                    t_fraction=0.80, noise_fraction=0.05
                                )
                                if secondary is not None:
                                    bx, by = state.points[base_choice]
                                    nx, ny = secondary
                                    state.points[base_choice] = (nx, ny)
                                    if min_distance_to_others(state.points, base_choice) < uniqueness_eps:
                                        state.points[base_choice] = (bx, by)
                                    else:
                                        state.reeval_point(base_choice)
                                        new2_min, _ = state.global_min()
                                        if new2_min >= current_min:
                                            current_min = new2_min
                                            accepted_this_iter = True
                                        else:
                                            # SA acceptance for secondary (weakened)
                                            delta2 = new2_min - current_min
                                            prob2 = math.exp(delta2 / max(T * 0.5, 1e-12)) if T > 0 else 0.0
                                            if rng.random() < prob2:
                                                current_min = new2_min
                                                accepted_this_iter = True
                                            else:
                                                # revert secondary
                                                state.points[base_choice] = (bx, by)
                                                state.reeval_point(base_choice)
                        else:
                            # Revert and restore areas by re-evaluating
                            state.points[idx_choice] = (oldx, oldy)
                            state.reeval_point(idx_choice)

            elif rpick < p_targeted + p_subgrad:
                # Soft-min multi-point subgradient step (coordinated small moves)
                did, new_min = subgradient_step(current_min, temp_scale, T)
                if did:
                    current_min = new_min
                    accepted_this_iter = True
            else:
                # Random exploration in barycentric space
                idx = rng.randrange(npts)
                ox, oy = state.points[idx]
                rx, ry = random_barycentric_perturb(state.points, idx, radius=0.025 * (temp_scale ** 0.7), rng=rng, eps=eps_inside)
                rx, ry = ensure_point_inside(rx, ry, eps=eps_inside)
                state.points[idx] = (rx, ry)
                if min_distance_to_others(state.points, idx) < uniqueness_eps:
                    state.points[idx] = (ox, oy)
                else:
                    state.reeval_point(idx)
                    new_min, _ = state.global_min()
                    if new_min >= current_min:
                        current_min = new_min
                        accepted_this_iter = True
                    else:
                        delta = new_min - current_min
                        prob = math.exp(delta / max(T, 1e-12)) if T > 0 else 0.0
                        if rng.random() < prob:
                            current_min = new_min
                            accepted_this_iter = True
                        else:
                            state.points[idx] = (ox, oy)
                            state.reeval_point(idx)

            # Update best-so-far
            if current_min > best_min:
                best_min = current_min
                best_pts = [list(p) for p in state.points]

            # Step-size adaptation
            tried_in_window += 1
            if accepted_this_iter:
                acc_in_window += 1
            if tried_in_window >= window:
                acc_ratio = acc_in_window / max(1, tried_in_window)
                if acc_ratio > 0.60:
                    base_step *= 1.12
                elif acc_ratio < 0.25:
                    base_step *= 0.86
                # Clamp base_step
                base_step = max(0.008, min(0.12, base_step))
                # Reset window counters
                acc_in_window = 0
                tried_in_window = 0

            # Additional periodic random micro-perturbation to escape traps
            if (t + 1) % 35 == 0:
                idx = rng.randrange(npts)
                ox, oy = state.points[idx]
                rx, ry = random_barycentric_perturb(state.points, idx, radius=0.015 * (temp_scale ** 0.5), rng=rng, eps=eps_inside)
                rx, ry = ensure_point_inside(rx, ry, eps=eps_inside)
                state.points[idx] = (rx, ry)
                if min_distance_to_others(state.points, idx) < 1e-7:
                    state.points[idx] = (ox, oy)
                    continue
                state.reeval_point(idx)
                new_min, _ = state.global_min()
                if new_min >= current_min:
                    current_min = new_min
                    if current_min > best_min:
                        best_min = current_min
                        best_pts = [list(p) for p in state.points]
                else:
                    # Weak SA acceptance
                    delta = new_min - current_min
                    prob = math.exp(delta / max(T * 0.5, 1e-12)) if T > 0 else 0.0
                    if rng.random() < prob:
                        current_min = new_min
                        if current_min > best_min:
                            best_min = current_min
                            best_pts = [list(p) for p in state.points]
                    else:
                        state.points[idx] = (ox, oy)
                        state.reeval_point(idx)

        # Post-optimization polish: deterministic pattern search and micro-moves
        state.points = [tuple(p) for p in best_pts]
        state.recompute_all()
        current_min, _ = state.global_min()

        # Pattern search: try a set of directions with decreasing step sizes
        directions = []
        for k in range(12):
            ang = 2.0 * math.pi * (k / 12.0)
            directions.append((math.cos(ang), math.sin(ang)))

        step = 0.025
        for outer in range(10):
            improved_any = False
            for idx in range(npts):
                for d in directions:
                    dx, dy = d[0] * step, d[1] * step
                    ox, oy = state.points[idx]
                    nx, ny = ensure_point_inside(ox + dx, oy + dy, eps=1e-6)
                    state.points[idx] = (nx, ny)
                    if min_distance_to_others(state.points, idx) < 1e-7:
                        state.points[idx] = (ox, oy)
                        continue
                    state.reeval_point(idx)
                    new_min, _ = state.global_min()
                    if new_min > current_min + 1e-12:
                        current_min = new_min
                        improved_any = True
                        if current_min > best_min:
                            best_min = current_min
                            best_pts = [list(p) for p in state.points]
                    else:
                        # revert
                        state.points[idx] = (ox, oy)
                        state.reeval_point(idx)
            step *= 0.6
            if not improved_any and step < 0.004:
                break

        # Worst-triangle micro-moves: push vertices along the normal only
        state.points = [tuple(p) for p in best_pts]
        state.recompute_all()
        current_min, _ = state.global_min()
        micro_step = 0.015
        for it in range(320):
            # Reduce step slowly
            micro_step *= 0.98
            # Determine current worst triangle
            amin, tri_idx = state.global_min()
            tri = state.combos[tri_idx]
            # Try pure normal moves for each vertex; pick best
            best_improvement = 0.0
            best_mod = None
            for idx_choice in tri:
                # Use move generator but with no noise, minimal tangential
                proposal = make_move_on_worst(state.points, tri, idx_choice, micro_step,
                                              rng=random.Random(1234 + it + idx_choice),
                                              eps_inside=1e-6,
                                              t_fraction=0.0, noise_fraction=0.0)
                if proposal is None:
                    continue
                nx, ny = proposal
                ox, oy = state.points[idx_choice]
                state.points[idx_choice] = (nx, ny)
                if min_distance_to_others(state.points, idx_choice) < 1e-7:
                    state.points[idx_choice] = (ox, oy)
                    continue
                state.reeval_point(idx_choice)
                new_min, _ = state.global_min()
                if new_min - current_min > best_improvement + 1e-15:
                    best_improvement = new_min - current_min
                    best_mod = (idx_choice, (nx, ny))
                # revert local change after evaluation
                state.points[idx_choice] = (ox, oy)
                state.reeval_point(idx_choice)
            if best_mod is not None and best_improvement > 0.0:
                idx_choice, (nx, ny) = best_mod
                state.points[idx_choice] = (nx, ny)
                state.reeval_point(idx_choice)
                current_min, _ = state.global_min()
                if current_min > best_min:
                    best_min = current_min
                    best_pts = [list(p) for p in state.points]
            else:
                if micro_step < 0.003:
                    break

        return best_pts, best_min

    def find_best_placement(npts):
        # Multiple restarts to escape local minima
        base_seed = 20231111
        restarts = 14
        best_overall_pts = None
        best_overall_min = -1.0
        for r in range(restarts):
            seed = base_seed + r * 997
            mode = "lattice" if (r % 2 == 0) else "poisson"
            pts, min_area = anneal_single_restart(npts, seed, init_mode=mode)
            if min_area > best_overall_min:
                best_overall_min = min_area
                best_overall_pts = pts
        return best_overall_pts, best_overall_min

    points, minimum_area = find_best_placement(11)
    # Ensure data types are basic lists for JSON compatibility
    points_out = [list(p) for p in points]
    return points_out, minimum_area
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))
```
