Safeguarded anisotropic squeeze about MEC center with acceptance on exact D_max and annealed gamma scheduling.

- Hull-Aligned Affine Squeeze Micro-step: Adding a safeguarded anisotropic rank-1 squeeze A = I − gamma·(u u^T) about the exact Minimum Enclosing Circle center, with u aggregated from all_active_diameter_pairs using an annealed tol_rel, coherently shortened near-co-maximal diameters without degrading D_min; the move was accepted only if the exact hull D_max did not increase, with trust-radius clipping and gamma backtracking (halving up to max_backtrack), and it was scheduled after caliper_shrink_backtrack and mec_support_squeeze plus 8–12 small-gamma polish passes; this geometry-aligned contraction helped break plateaus on (n=16, d=2), achieving ratio_squared 12.903139580668318 and score 0.9989247989931765, suggesting future designs should reuse MEC-centered major-axis squeezes with strict D_max-monotone acceptance and annealed gamma (≈0.030→0.006, then ≈0.008 for polish).

```python
#!/usr/bin/env python3
"""Optimizes 16 planar points to minimize (D_max / D_min)^2 using a hybrid
multi-island projection-based optimizer integrating calipers-based diameter
shrink with backtracking acceptance, MEC/hull guidance, and ring-parameter
micro-CMA proposals.

We enforce D_min >= 1 via local pairwise projection and gentle normalization
and then minimize the squared diameter D_max^2, which equals R^2 once D_min == 1.

This version integrates three key mutations:
1) Exact MEC support squeeze micro-step:
   - Computes the exact Minimum Enclosing Circle (MEC) with its support set (2 or 3 hull points).
   - Slightly moves all non-support hull points inward toward the MEC center; interior points move
     inward with half step. Support points are frozen. The move is accepted only if the exact
     diameter (D_max) does not increase.
2) Adaptive active-pair tolerance for calipers:
   - Widen the active set early to include near-co-maximal hull pairs via a relative tolerance
     tol_rel that anneals from ~5e-3 to 1e-4 over the schedule, improving stability and convergence.
3) Affine major-axis anisotropic squeeze micro-step:
   - Using the MEC center and active near-diameter pairs, compute a coherent major axis u and apply
     a rank-1 affine contraction A = I − gamma·(u u^T) about the MEC center. This shortens all
     co-aligned near-max diameters at once. Displacements are trust-clipped and feasibility is
     repaired. The move is accepted only if exact D_max does not increase, with backtracked gamma.

Main features:
- Geometry-aware seeds and multi-island engines with diversification and migration.
- Feasibility via local projection-repair and light normalization to set D_min ≈ 1.
- Exact evaluation on the hull via farthest pair (brute over hull).
- Island A (Smooth-Projected Caliper Descent), Island B (MEC/Hull-Guided Contraction),
  and Island C (Ring-Param Micro-CMA) work in concert.
- Final polish uses multiple calipers shrinks, MEC support squeezes, and several
  affine major-axis squeezes interleaved.

Returns:
- points: (n x 2) NumPy array of final coordinates
- ratio_squared: float value of (max distance / min distance)^2, with D_min == 1
"""

import json
import itertools
from math import sqrt, sin, cos, pi
import numpy as np


# EVOLVE_START
def optimize_construct(n=16, d=2, random_seed=42):
    if (n, d) != (16, 2):
        raise ValueError("the benchmark evaluates n=16 and d=2")

    rng = np.random.default_rng(random_seed)

    # ------------------------------
    # Utility functions
    # ------------------------------
    def pairwise_distances(X):
        diff = X[:, None, :] - X[None, :, :]
        D = np.linalg.norm(diff, axis=-1)
        return D

    def min_nonzero_distance(D):
        m = D.shape[0]
        mask = ~np.eye(m, dtype=bool)
        nonzero = D[mask]
        return float(np.min(nonzero))

    def max_distance(D):
        m = D.shape[0]
        mask = ~np.eye(m, dtype=bool)
        return float(np.max(D[mask]))

    def centroid(X):
        return np.mean(X, axis=0)

    def recenter(X):
        c = centroid(X)
        X -= c
        return X

    def rotation(theta):
        c, s = np.cos(theta), np.sin(theta)
        return np.array([[c, -s],
                         [s,  c]])

    def rescale_to_min_one(X, tol=1e-12):
        # Light global normalization to set D_min to 1 exactly
        D = pairwise_distances(X)
        dmin = min_nonzero_distance(D)
        if dmin <= 0:
            return X  # degenerate guard
        if abs(dmin - 1.0) > tol:
            X *= (1.0 / dmin)
        return X

    def project_feasible(X, max_passes=5, tol=1e-12):
        # Sequentially push violating pairs apart until all dij >= 1
        X = X.copy()
        m = X.shape[0]
        for _ in range(max_passes):
            D = pairwise_distances(X)
            viol = np.argwhere(D < 1.0 - tol)
            if viol.size == 0:
                break
            rng.shuffle(viol)
            for i, j in viol:
                if i >= j:
                    continue
                dij = D[i, j]
                if dij <= tol:
                    # Extremely close or identical numerically; random push
                    dir_vec = rng.normal(size=2)
                    norm = np.linalg.norm(dir_vec)
                    dir_unit = dir_vec / max(norm, 1e-12)
                    delta = 0.5
                    X[i] -= delta * dir_unit
                    X[j] += delta * dir_unit
                    continue
                dir_unit = (X[j] - X[i]) / dij
                delta = 0.5 * (1.0 - dij)
                X[i] -= delta * dir_unit
                X[j] += delta * dir_unit
        # Light normalization and recentering
        X = rescale_to_min_one(X)
        recenter(X)
        return X

    # ------------------------------
    # Convex hull and diameter (rotating calipers equivalent)
    # ------------------------------
    def _cross(o, a, b):
        # Cross product (OA x OB)
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def convex_hull_indices(X):
        # Monotone chain; CCW order
        pts = X
        npts = pts.shape[0]
        idx_sorted = np.lexsort((pts[:, 1], pts[:, 0]))
        # Build lower
        lower = []
        for idx in idx_sorted:
            while len(lower) >= 2 and _cross(pts[lower[-2]], pts[lower[-1]], pts[idx]) <= 1e-12:
                lower.pop()
            lower.append(idx)
        # Build upper
        upper = []
        for idx in reversed(idx_sorted):
            while len(upper) >= 2 and _cross(pts[upper[-2]], pts[upper[-1]], pts[idx]) <= 1e-12:
                upper.pop()
            upper.append(idx)
        hull = lower[:-1] + upper[:-1]
        # Deduplicate preserving order
        seen = set()
        hull_unique = []
        for i in hull:
            if i not in seen:
                hull_unique.append(i)
                seen.add(i)
        if len(hull_unique) == 0:
            return list(range(npts))
        return hull_unique

    def farthest_pair_indices(X):
        # Compute farthest pair using only hull vertices (exact in 2D), brute force over hull
        hull = convex_hull_indices(X)
        h = len(hull)
        if h <= 1:
            return 0, 0, 0.0
        best = (hull[0], hull[1], 0.0)
        bestd2 = -1.0
        for a in range(h):
            i = hull[a]
            for b in range(a + 1, h):
                j = hull[b]
                dij2 = float(np.dot(X[j] - X[i], X[j] - X[i]))
                if dij2 > bestd2:
                    bestd2 = dij2
                    best = (i, j, sqrt(dij2))
        return best

    # ------------------------------
    # Exact Minimum Enclosing Circle (MEC) with support set
    # ------------------------------
    def _circumcircle(p, q, r):
        # Return center and radius for circle through p, q, r; None if nearly collinear
        A = q - p
        B = r - p
        Ax, Ay = A
        Bx, By = B
        denom = 2.0 * (Ax * By - Ay * Bx)
        if abs(denom) < 1e-12:
            return None, None
        # Compute circumcenter relative to p
        a2 = Ax * Ax + Ay * Ay
        b2 = Bx * Bx + By * By
        ux = (By * a2 - Ay * b2) / denom
        uy = (Ax * b2 - Bx * a2) / denom
        c = p + np.array([ux, uy])
        R = float(np.linalg.norm(c - p))
        return c, R

    def mec_circle(X):
        # Compute exact MEC using brute-force over 2- and 3-point supports on the hull
        m = X.shape[0]
        hull = convex_hull_indices(X)
        h = len(hull)
        if h == 0:
            return np.zeros(2), 0.0, []
        # Helper to test enclosure
        def encloses(center, radius):
            if center is None:
                return False
            d = np.linalg.norm(X - center[None, :], axis=1)
            return bool(np.all(d <= radius + 1e-9))
        best_c = None
        best_R = float('inf')
        best_S = []

        # Try all pairs (2-point support)
        for a in range(h):
            i = hull[a]
            for b in range(a + 1, h):
                j = hull[b]
                c = 0.5 * (X[i] + X[j])
                R = 0.5 * float(np.linalg.norm(X[j] - X[i]))
                if encloses(c, R) and R < best_R - 1e-12:
                    best_R = R
                    best_c = c
                    best_S = [i, j]

        # Try all triples (3-point support)
        for a in range(h):
            i = hull[a]
            for b in range(a + 1, h):
                j = hull[b]
                for cidx in range(b + 1, h):
                    k = hull[cidx]
                    c, R = _circumcircle(X[i], X[j], X[k])
                    if c is None:
                        continue
                    if encloses(c, R) and R < best_R - 1e-12:
                        best_R = R
                        best_c = c
                        best_S = [i, j, k]

        # Fallback: if none found due to numeric issues, use farthest pair midpoint
        if best_c is None:
            i, j, d = farthest_pair_indices(X)
            best_c = 0.5 * (X[i] + X[j])
            best_R = 0.5 * d
            best_S = [i, j]
        return best_c, float(best_R), best_S

    # ------------------------------
    # Active diameter pairs (adaptive tolerance)
    # ------------------------------
    def all_active_diameter_pairs(X, tol_rel=None, tol_abs=1e-12):
        # Returns list of (i,j) pairs on the hull whose distance is within a relative tolerance
        # of D_max. If tol_rel is None, falls back to absolute proximity criterion.
        hull = convex_hull_indices(X)
        h = len(hull)
        if h <= 1:
            return [], 0.0
        dmax2 = -1.0
        dists2 = {}
        for a in range(h):
            i = hull[a]
            for b in range(a + 1, h):
                j = hull[b]
                dij2 = float(np.dot(X[j] - X[i], X[j] - X[i]))
                dists2[(i, j)] = dij2
                if dij2 > dmax2:
                    dmax2 = dij2
        dmax = sqrt(dmax2)
        active = []
        if tol_rel is None:
            # Previous behavior: include within tol_abs * dmax2 (absolute)
            for (i, j), dij2 in dists2.items():
                if dmax2 - dij2 <= max(tol_abs * dmax2, 1e-12):
                    active.append((i, j))
        else:
            thr = (1.0 - max(0.0, tol_rel)) * dmax2
            for (i, j), dij2 in dists2.items():
                if dij2 >= thr - 1e-15:
                    active.append((i, j))
        return active, dmax

    def true_ratio_sq(X):
        # Ensure D_min == 1 via a light normalization and compute D_max^2
        Xn = rescale_to_min_one(X.copy())
        _, _, dmax = farthest_pair_indices(Xn)
        return float(dmax * dmax)

    # ------------------------------
    # Calipers-based shrink with backtracking (adaptive active set)
    # ------------------------------
    def caliper_shrink_backtrack(X, eps=0.02, max_backtrack=12, tol_rel=None):
        # Compute current exact D_max on hull
        X0 = X.copy()
        X0 = project_feasible(X0, max_passes=3)
        _, _, dmax0 = farthest_pair_indices(X0)
        if dmax0 <= 1e-12:
            return X0, False
        step = float(eps)
        accepted = False
        for _ in range(max_backtrack):
            # Aggregate displacements across all active pairs (adaptive)
            pairs, _ = all_active_diameter_pairs(X0, tol_rel=tol_rel)
            if len(pairs) == 0:
                break
            disp = np.zeros_like(X0)
            counts = np.zeros(X0.shape[0], dtype=int)
            for (i, j) in pairs:
                v = X0[j] - X0[i]
                d = np.linalg.norm(v)
                if d <= 1e-12:
                    continue
                u = v / d
                # Move both endpoints towards midpoint by fraction 'step'
                disp[i] += step * u
                disp[j] -= step * u
                counts[i] += 1
                counts[j] += 1
            # Average displacements for points participating in multiple pairs
            for idx in range(X0.shape[0]):
                if counts[idx] > 0:
                    disp[idx] /= float(max(counts[idx], 1))
            Xcand = X0 + disp
            # Gentle inward pull for outermost hull points to assist shrink (approx MEC center)
            hull = convex_hull_indices(Xcand)
            if len(hull) > 0:
                i_fp, j_fp, dmax_fp = farthest_pair_indices(Xcand)
                c = 0.5 * (Xcand[i_fp] + Xcand[j_fp])
                R = max(0.5 * dmax_fp, 1e-9)
                rads = np.linalg.norm(Xcand[hull] - c, axis=1)
                idx_sorted = np.argsort(-rads)
                for hidx in idx_sorted[:min(6, len(hull))]:
                    idx = hull[hidx]
                    r = np.linalg.norm(Xcand[idx] - c)
                    if r > 1e-12:
                        Xcand[idx] -= 0.15 * step * (Xcand[idx] - c) / r
            # Project and normalize
            Xcand = project_feasible(Xcand, max_passes=4)
            # Acceptance on exact D_max
            _, _, dmax_cand = farthest_pair_indices(Xcand)
            if dmax_cand <= dmax0 + 1e-12:
                X0 = Xcand
                dmax0 = dmax_cand
                accepted = True
                break
            else:
                step *= 0.5
        return X0, accepted

    # ------------------------------
    # MEC support squeeze micro-step
    # ------------------------------
    def mec_support_squeeze(X, eta=0.02, trust=0.05, prev_dmax=None):
        # Compute exact MEC, move all non-support hull points slightly inward toward its center.
        # Interior points move with half step. Support points are frozen.
        # Accept only if exact diameter does not increase.
        X0 = X.copy()
        if prev_dmax is None:
            _, _, prev_dmax = farthest_pair_indices(X0)
        c, R, S = mec_circle(X0)
        Sset = set(S)
        hull = convex_hull_indices(X0)
        hset = set(hull)
        disp = np.zeros_like(X0)
        for idx in range(X0.shape[0]):
            if idx in Sset:
                continue  # freeze support points
            # Direction toward MEC center
            vec = X0[idx] - c
            r = float(np.linalg.norm(vec))
            if r <= 1e-12:
                continue
            step = eta * (0.5 if idx not in hset else 1.0)
            delta = -step * (vec / r)
            # Clip by trust radius
            dn = float(np.linalg.norm(delta))
            if dn > trust:
                delta = delta * (trust / dn)
            disp[idx] += delta
        Xcand = X0 + disp
        Xcand = project_feasible(Xcand, max_passes=4)
        _, _, dmax_cand = farthest_pair_indices(Xcand)
        if dmax_cand <= prev_dmax + 1e-12:
            return Xcand, True
        return X0, False

    # ------------------------------
    # Affine major-axis anisotropic squeeze micro-step
    # ------------------------------
    def affine_major_axis_squeeze(X, gamma=0.02, tol_rel=1e-3, trust=0.05, max_backtrack=8, prev_dmax=None):
        """Coherently contract along the current major axis u determined by active near-max diameter pairs.
        A = I - gamma * (u u^T) is applied about the exact MEC center. Accept only if exact D_max does not increase.
        Backtrack gamma by halving if rejected.
        """
        X0 = X.copy()
        # Baseline diameter for acceptance
        if prev_dmax is None:
            _, _, prev_dmax = farthest_pair_indices(X0)
        # Compute MEC center
        c_star, _, _ = mec_circle(X0)
        # Determine active near-max diameter pairs and construct major axis direction u
        pairs, _ = all_active_diameter_pairs(X0, tol_rel=tol_rel)
        u = None
        if len(pairs) > 0:
            sum_vec = np.zeros(2)
            sum_norms = 0.0
            for (i, j) in pairs:
                v = X0[j] - X0[i]
                sum_vec += v
                sum_norms += float(np.linalg.norm(v))
            s_norm = float(np.linalg.norm(sum_vec))
            # Detect near-isotropy: significant cancellation among directions
            ratio = s_norm / max(sum_norms, 1e-12)
            if ratio >= 0.15:  # reasonably coherent
                u = sum_vec / max(s_norm, 1e-12)
        if u is None:
            # Fallback to farthest-pair direction
            i_fp, j_fp, d_fp = farthest_pair_indices(X0)
            if d_fp > 1e-12:
                u = (X0[j_fp] - X0[i_fp]) / d_fp
            else:
                u = np.array([1.0, 0.0])
        # Ensure unit vector
        un = float(np.linalg.norm(u))
        if un <= 1e-12:
            return X0, False
        u = u / un

        # Backtracking loop on gamma
        g = float(max(0.0, gamma))
        accepted = False
        Ybest = X0
        for _ in range(max_backtrack):
            # Build affine contraction A = I - g * (u u^T)
            A = np.eye(2) - g * np.outer(u, u)
            # Apply about MEC center and trust-clip displacements
            Xshift = X0 - c_star
            Yraw = c_star + Xshift @ A.T
            disp = Yraw - X0
            norms = np.linalg.norm(disp, axis=1)
            # Clip displacement magnitudes to trust radius
            clip = np.minimum(1.0, trust / np.maximum(norms, 1e-12))
            disp = disp * clip[:, None]
            Ycand = X0 + disp
            # Project feasibility and normalize
            Ycand = project_feasible(Ycand, max_passes=4)
            # Exact D_max acceptance
            _, _, dmax_cand = farthest_pair_indices(Ycand)
            if dmax_cand <= prev_dmax + 1e-12:
                Ybest = Ycand
                accepted = True
                break
            else:
                g *= 0.5
        return (Ybest, True) if accepted else (X0, False)

    # ------------------------------
    # Triangular lattice (hex grid) utilities
    # ------------------------------
    sqrt3 = sqrt(3.0)

    def axial_to_xy(q, r):
        # Basis: e1=(1,0), e2=(1/2, sqrt(3)/2)
        return np.array([q + 0.5 * r, 0.5 * sqrt3 * r])

    def hex_ring_coords(k):
        # Return axial (q,r) on the hex ring of axial norm k
        if k == 0:
            return [(0, 0)]
        results = []
        q, r = (k, 0)
        steps = [(0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1), (1, 0)]
        for dq, dr in steps:
            for _ in range(k):
                results.append((q, r))
                q += dq
                r += dr
        return results

    def triangular_patch_top_169(top_k=4):
        # Construct 1 + 6 + 9 by enumerating 9 of 12 nodes on ring-2; return top-K by D_max
        center = [(0, 0)]
        ring1 = hex_ring_coords(1)  # 6 nodes
        ring2 = hex_ring_coords(2)  # 12 nodes
        c_xy = [axial_to_xy(q, r) for (q, r) in center]
        r1_xy = [axial_to_xy(q, r) for (q, r) in ring1]
        r2_xy = [axial_to_xy(q, r) for (q, r) in ring2]
        r2_indices = list(range(len(r2_xy)))
        candidates = []
        for subset in itertools.combinations(r2_indices, 9):
            pts = np.vstack([c_xy, r1_xy, [r2_xy[i] for i in subset]])
            pts = rescale_to_min_one(pts)
            _, _, d = farthest_pair_indices(pts)
            candidates.append((d, pts))
        # Select top-K with smallest diameter
        candidates.sort(key=lambda t: t[0])
        top = [candidates[i][1] for i in range(min(top_k, len(candidates)))]
        return top

    # ------------------------------
    # Deterministic hex-lattice seeds
    # ------------------------------
    def hex_lattice_seed(theta=0.0, offset=(0.0, 0.0), radius_qr=4):
        # Build a rotated hex lattice, select 16 nodes nearest to a given offset,
        # then recenter. Unit neighbor distance is 1 by construction.
        coords = []
        for q in range(-radius_qr, radius_qr + 1):
            for r in range(-radius_qr, radius_qr + 1):
                coords.append(axial_to_xy(q, r))
        coords = np.array(coords)
        # Rotate lattice
        R = rotation(theta)
        coords = coords @ R.T
        # Apply offset (fractional within unit cell) in the rotated frame
        t = np.array(offset, dtype=float)
        coords_off = coords + t
        # Choose 16 points closest to origin
        d2 = np.sum(coords_off**2, axis=1)
        idx = np.argsort(d2)[:16]
        pts = coords_off[idx]
        pts = recenter(pts)
        pts = rescale_to_min_one(pts)
        return pts

    # ------------------------------
    # Ring templates (multi-ring)
    # ------------------------------
    def ring_radius_for_k(k):
        # Minimal radius so that adjacent arc distance (chord) >= 1: chord = 2r sin(pi/k)
        # Set chord == 1 => r = 1/(2 sin(pi/k))
        return 1.0 / (2.0 * sin(pi / k))

    def ring_points(k, r, phi0=0.0):
        pts = []
        for m in range(k):
            th = phi0 + 2 * pi * m / k
            pts.append([r * cos(th), r * sin(th)])
        return np.array(pts)

    def two_ring_seed(k1, k2, scale=1.0, offset2=0.5):
        # Construct two-ring pattern with k1 + k2 = 16
        r1 = scale * ring_radius_for_k(k1)
        r2 = scale * ring_radius_for_k(k2)
        phi1 = 0.0
        phi2 = offset2 * (pi / max(k1, 1))
        inner = ring_points(k1, r1, phi1)
        outer = ring_points(k2, r2, phi2)
        pts = np.vstack([inner, outer])
        pts = project_feasible(pts, max_passes=6)
        return pts

    # ------------------------------
    # Poisson-like seeds
    # ------------------------------
    def poisson_seed(m=16, target_radius=2.2):
        pts = []
        for _ in range(m):
            r = target_radius * sqrt(rng.uniform(0.0, 1.0))
            theta = rng.uniform(0.0, 2 * pi)
            pts.append([r * np.cos(theta), r * np.sin(theta)])
        X = np.array(pts)
        X = project_feasible(X, max_passes=10)
        return X

    # ------------------------------
    # Island engines
    # ------------------------------
    class Island:
        def __init__(self, kind, X, name=""):
            self.kind = kind  # 'A', 'B', or 'C'
            self.X = project_feasible(X, max_passes=6)
            self.bestX = self.X.copy()
            self.best_val = true_ratio_sq(self.X)
            self.iter = 0
            self.name = name if name else kind

        def step(self, t_global, total_steps):
            if self.kind == 'A':
                self._step_A(t_global, total_steps)
            elif self.kind == 'B':
                self._step_B(t_global, total_steps)
            elif self.kind == 'C':
                self._step_C(t_global, total_steps)
            else:
                pass

        def _alpha_schedule(self, t, T, a0, a1):
            # Linear schedule from a0 to a1 across T iterations of this island
            frac = min(1.0, max(0.0, t / max(T, 1)))
            return a0 * (1.0 - frac) + a1 * frac

        def _update_best(self):
            val = true_ratio_sq(self.X)
            if val < self.best_val - 1e-10:
                self.best_val = val
                self.bestX = self.X.copy()

        def _step_A(self, t_global, total_steps):
            # Smooth-Projected Caliper Descent + MEC support squeeze + affine major-axis squeeze
            X = self.X.copy()
            alpha = self._alpha_schedule(self.iter, max(1, total_steps), 0.04, 0.006)
            trust = self._alpha_schedule(self.iter, max(1, total_steps), 0.06, 0.02)
            tol_rel = self._alpha_schedule(self.iter, max(1, total_steps), 5e-3, 1e-4)
            gamma = self._alpha_schedule(self.iter, max(1, total_steps), 0.030, 0.006)

            # Farthest pair contraction and mild inward pulls
            i, j, dij = farthest_pair_indices(X)
            if dij > 1e-12:
                u = (X[j] - X[i]) / dij
                X[i] += alpha * u
                X[j] -= alpha * u
            r = np.linalg.norm(X, axis=1)
            k = min(6, X.shape[0])
            idx_sorted = np.argsort(-r)
            for idx in idx_sorted[:k]:
                if r[idx] > 1e-12:
                    X[idx] -= (alpha * 0.2) * (X[idx] / r[idx])

            # Projection repair
            X = project_feasible(X, max_passes=4)
            # Calipers shrink with backtracking (adaptive active set)
            X_shrunk, accepted = caliper_shrink_backtrack(X, eps=alpha * 0.9, max_backtrack=10, tol_rel=tol_rel)
            if accepted:
                X = X_shrunk

            # Affine major-axis squeeze about MEC center; accept only if D_max non-increasing
            _, _, dmax_prev = farthest_pair_indices(X)
            X_aff, ok_aff = affine_major_axis_squeeze(X, gamma=gamma, tol_rel=tol_rel, trust=trust, max_backtrack=8, prev_dmax=dmax_prev)
            if ok_aff:
                X = X_aff

            # MEC support squeeze micro-step (annealed eta, clipped by trust), accept non-worsening D_max
            _, _, dmax_prev = farthest_pair_indices(X)
            eta = self._alpha_schedule(self.iter, max(1, total_steps), 0.03, 0.006)
            X_squeezed, ok = mec_support_squeeze(X, eta=eta, trust=trust, prev_dmax=dmax_prev)
            if ok:
                X = X_squeezed

            # Follow-up affine squeeze to reduce lingering co-aligned near-max pairs
            _, _, dmax_prev = farthest_pair_indices(X)
            X_aff2, ok_aff2 = affine_major_axis_squeeze(X, gamma=gamma * 0.8, tol_rel=tol_rel, trust=trust, max_backtrack=6, prev_dmax=dmax_prev)
            if ok_aff2:
                X = X_aff2

            # Commit and update best
            self.X = X
            self._update_best()
            self.iter += 1

        def _step_B(self, t_global, total_steps):
            # MEC/Hull-guided contraction with repulsions, calipers shrink, MEC support squeeze,
            # and affine major-axis squeeze
            X = self.X.copy()
            lam = self._alpha_schedule(self.iter, max(1, total_steps), 0.6, 0.15)
            step = self._alpha_schedule(self.iter, max(1, total_steps), 0.06, 0.008)
            trust = self._alpha_schedule(self.iter, max(1, total_steps), 0.08, 0.02)
            tol_rel = self._alpha_schedule(self.iter, max(1, total_steps), 4e-3, 1e-4)
            gamma = self._alpha_schedule(self.iter, max(1, total_steps), 0.030, 0.006)

            # Farthest pair, approx MEC center
            i, j, dmax = farthest_pair_indices(X)
            if dmax < 1e-12:
                self.iter += 1
                return
            c = 0.5 * (X[i] + X[j])
            R = 0.5 * dmax
            # Velocities
            V = np.zeros_like(X)
            # Repulsion for near contacts
            D = pairwise_distances(X)
            m = X.shape[0]
            for a in range(m):
                for b in range(a + 1, m):
                    dij = D[a, b]
                    if dij < 1.1:
                        dir_unit = (X[a] - X[b]) / max(dij, 1e-12)
                        strength = max(0.0, 1.1 - dij)
                        if dij < 1.0:
                            strength *= 3.0
                        V[a] += strength * dir_unit
                        V[b] -= strength * dir_unit
            # Hull inward pull
            hull = convex_hull_indices(X)
            hull_set = set(hull)
            for idx in range(m):
                weight = lam * (1.0 if idx in hull_set else 0.5)
                V[idx] += -weight * (X[idx] - c) / max(R, 1e-9)
            # Integrate with trust radius and small early noise
            noise_scale = 0.05 * self._alpha_schedule(self.iter, max(1, total_steps), 1.0, 0.0)
            if noise_scale > 1e-6:
                V += noise_scale * rng.normal(size=V.shape)
            disp = step * V
            norms = np.linalg.norm(disp, axis=1, keepdims=True)
            clip = np.minimum(1.0, trust / np.maximum(norms, 1e-12))
            disp = disp * clip
            X = X + disp
            # Periodic squeeze: slightly pull longest hull edge endpoints inward
            if (self.iter % 25) == 0 and len(hull) >= 2:
                hcoords = X[hull]
                lengths = np.linalg.norm(np.roll(hcoords, -1, axis=0) - hcoords, axis=1)
                idx_long = int(np.argmax(lengths))
                a = hull[idx_long]
                b = hull[(idx_long + 1) % len(hull)]
                for idx in (a, b):
                    r = np.linalg.norm(X[idx] - c)
                    if r > 1e-12:
                        X[idx] -= 0.15 * step * (X[idx] - c) / r
            # Project and normalize
            X = project_feasible(X, max_passes=5)
            # Calipers shrink with backtracking (adaptive active set)
            X_shrunk, accepted = caliper_shrink_backtrack(X, eps=step * 0.8, max_backtrack=10, tol_rel=tol_rel)
            if accepted:
                X = X_shrunk

            # Affine major-axis squeeze about MEC center; accept only if D_max non-increasing
            _, _, dmax_prev = farthest_pair_indices(X)
            X_aff, ok_aff = affine_major_axis_squeeze(X, gamma=gamma, tol_rel=tol_rel, trust=trust, max_backtrack=8, prev_dmax=dmax_prev)
            if ok_aff:
                X = X_aff

            # MEC support squeeze micro-step (annealed eta, clipped by trust)
            _, _, dmax_prev = farthest_pair_indices(X)
            eta = self._alpha_schedule(self.iter, max(1, total_steps), 0.03, 0.006)
            X_squeezed, ok = mec_support_squeeze(X, eta=eta, trust=trust, prev_dmax=dmax_prev)
            if ok:
                X = X_squeezed

            # Follow-up affine squeeze to reduce lingering co-aligned near-max pairs
            _, _, dmax_prev = farthest_pair_indices(X)
            X_aff2, ok_aff2 = affine_major_axis_squeeze(X, gamma=gamma * 0.8, tol_rel=tol_rel, trust=trust, max_backtrack=6, prev_dmax=dmax_prev)
            if ok_aff2:
                X = X_aff2

            self.X = X
            self._update_best()
            self.iter += 1

        def _step_C(self, t_global, total_steps):
            # Ring-Param Micro-CMA: sample several perturbations and keep best
            patterns = [(8, 8), (6, 10), (7, 9), (5, 11)]
            k1, k2 = patterns[self.iter % len(patterns)]
            # Base params
            base_scale = 1.0 + 0.05 * rng.standard_normal()
            base_off = float(np.mod(0.5 + 0.2 * rng.standard_normal(), 1.0))
            # Sample candidates
            num_cand = 6
            best_cand = None
            best_val = np.inf
            for _ in range(num_cand):
                sc = base_scale + 0.04 * rng.standard_normal()
                off = float(np.mod(base_off + 0.2 * rng.standard_normal(), 1.0))
                cand = two_ring_seed(k1, k2, scale=max(0.94, sc), offset2=off)
                cand = project_feasible(cand, max_passes=3)
                val = true_ratio_sq(cand)
                if val < best_val:
                    best_val = val
                    best_cand = cand
            # Replace current if better
            if best_cand is not None and best_val < true_ratio_sq(self.X) - 1e-10:
                self.X = best_cand
                if best_val < self.best_val - 1e-10:
                    self.best_val = best_val
                    self.bestX = best_cand.copy()
            self.iter += 1

    # ------------------------------
    # Build diverse seeds
    # ------------------------------
    seeds = []

    # S1: Best triangular patch seeds (top-K ring-2 enumeration)
    tri_top = triangular_patch_top_169(top_k=5)
    for s in tri_top:
        seeds.append(s)
    # Rotated variants for diversity
    rot_thetas = [pi / 12, pi / 24, pi / 18]
    for th in rot_thetas:
        for s in tri_top[:2]:
            seeds.append(project_feasible(s @ rotation(th).T, max_passes=4))

    # S2: Deterministic hex-lattice seeds with varied orientation and offsets
    thetas = np.linspace(0.0, pi / 3, num=5, endpoint=False)
    offsets = [(0.0, 0.0), (0.25, 0.15)]
    for th in thetas:
        for off in offsets:
            seeds.append(hex_lattice_seed(theta=th, offset=off, radius_qr=4))

    # S3: Two-ring templates: 8–8, 6–10, 7–9 (and 5–11)
    ring_patterns = [(8, 8), (6, 10), (7, 9), (5, 11)]
    for (k1, k2) in ring_patterns:
        seeds.append(two_ring_seed(k1, k2, scale=1.0, offset2=0.5))
        seeds.append(two_ring_seed(k1, k2, scale=0.98, offset2=0.35))

    # S4: Poisson-disk-like seeds
    for _ in range(4):
        seeds.append(poisson_seed())

    # Cap number of seeds to manageable islands
    rng.shuffle(seeds)
    seeds = seeds[:16]

    # ------------------------------
    # Initialize islands
    # ------------------------------
    islands = []
    # Assign engines in round-robin A, B, A, B, C
    engine_cycle = ['A', 'B', 'A', 'B', 'C']
    for idx, seed in enumerate(seeds):
        kind = engine_cycle[idx % len(engine_cycle)]
        islands.append(Island(kind, seed, name=f"{kind}-{idx}"))

    # ------------------------------
    # Evolution loop with migration/annealing
    # ------------------------------
    total_steps = 480  # per island
    migrate_every = 40
    global_best_X = None
    global_best_val = np.inf

    def eval_island(isle):
        return true_ratio_sq(isle.X)

    # Helper: perturb elite
    def perturb(X):
        Y = X.copy()
        th = rng.uniform(-pi / 64, pi / 64)
        Y = Y @ rotation(th).T
        Y += 0.01 * rng.normal(size=Y.shape)
        Y = project_feasible(Y, max_passes=5)
        # One caliper shrink to polish (use moderate tol_rel)
        Y, _ = caliper_shrink_backtrack(Y, eps=0.02, max_backtrack=8, tol_rel=1e-3)
        return Y

    # Helper: topology restart
    def topology_restart_from_elite(Xelite):
        patterns = ring_patterns
        k1, k2 = patterns[rng.integers(0, len(patterns))]
        off = rng.uniform(0.0, 1.0)
        scale = 1.0 + 0.05 * rng.standard_normal()
        Y = two_ring_seed(k1, k2, scale=max(0.95, scale), offset2=off)
        th = rng.uniform(-pi / 16, pi / 16)
        Y = Y @ rotation(th).T
        Y = project_feasible(Y, max_passes=5)
        Y, _ = caliper_shrink_backtrack(Y, eps=0.02, max_backtrack=8, tol_rel=1e-3)
        return Y

    # Helper: crossover two elites
    def crossover(Xa, Xb):
        Ya = Xa.copy()
        Yb = Xb.copy()
        # Random rotations
        Ya = Ya @ rotation(rng.uniform(-pi / 32, pi / 32)).T
        Yb = Yb @ rotation(rng.uniform(-pi / 32, pi / 32)).T
        # Mix half points from each
        m = Ya.shape[0]
        mask = rng.random(m) < 0.5
        Y = np.where(mask[:, None], Ya, Yb)
        Y = project_feasible(Y, max_passes=6)
        Y, _ = caliper_shrink_backtrack(Y, eps=0.02, max_backtrack=8, tol_rel=1e-3)
        return Y

    # Main loop
    for t in range(total_steps):
        # Step each island
        for isle in islands:
            isle.step(t, total_steps)

        # Update global elite
        for isle in islands:
            if isle.best_val < global_best_val - 1e-12:
                global_best_val = isle.best_val
                global_best_X = isle.bestX.copy()

        # Migration and refresh
        if (t + 1) % migrate_every == 0:
            # Evaluate islands
            vals = [eval_island(isle) for isle in islands]
            order = np.argsort(vals)
            best_idx = int(order[0])
            second_idx = int(order[1]) if len(order) > 1 else best_idx
            worst_idx = int(order[-1])
            elite = islands[best_idx].bestX.copy()
            elite2 = islands[second_idx].bestX.copy()
            # Replace worst by a diversification move
            r = rng.uniform()
            if r < 0.4:
                newX = perturb(elite)
            elif r < 0.8:
                newX = topology_restart_from_elite(elite)
            else:
                newX = crossover(elite, elite2)
            # Switch engine kind randomly to diversify
            new_kind = rng.choice(['A', 'B', 'C'])
            islands[worst_idx] = Island(new_kind, newX, name=f"{new_kind}-reseed")
            # Occasionally reorder worst two
            if len(order) >= 2 and rng.uniform() < 0.25:
                worst2_idx = int(order[-2])
                islands[worst2_idx] = Island(rng.choice(['A', 'B', 'C']), perturb(elite2), name="reseed-2")

            # Small hull-only jitter to escape flat plateaus
            for jidx in order[:3]:
                Xj = islands[jidx].X
                hull = convex_hull_indices(Xj)
                if len(hull) > 0 and rng.uniform() < 0.3:
                    Xj[hull] += 0.005 * rng.normal(size=(len(hull), 2))
                    Xj = project_feasible(Xj, max_passes=3)
                    Xj, _ = caliper_shrink_backtrack(Xj, eps=0.01, max_backtrack=6, tol_rel=8e-4)
                    islands[jidx].X = Xj
                    islands[jidx]._update_best()

    # Final elite polish: feasibility pass + multiple calipers shrinks + MEC squeeze + affine squeezes
    final = global_best_X.copy() if global_best_X is not None else seeds[0]
    final = project_feasible(final, max_passes=8)
    for it in range(60):
        final, _ = caliper_shrink_backtrack(final, eps=0.015, max_backtrack=10, tol_rel=5e-4)
        final = project_feasible(final, max_passes=4)
        # Interleaved affine major-axis squeeze (about 10 passes total)
        if (it % 6) == 0:
            _, _, dmax_prev = farthest_pair_indices(final)
            gamma_polish = 0.008 * (1.0 - 0.5 * (it / 60.0))
            final_aff, ok_aff = affine_major_axis_squeeze(final, gamma=gamma_polish, tol_rel=5e-4, trust=0.02, max_backtrack=6, prev_dmax=dmax_prev)
            if ok_aff:
                final = final_aff
        # MEC micro-squeeze with small eta; accept only if D_max non-increasing
        _, _, dmax_prev = farthest_pair_indices(final)
        eta_polish = 0.008
        final_sq, ok = mec_support_squeeze(final, eta=eta_polish, trust=0.02, prev_dmax=dmax_prev)
        if ok:
            final = final_sq
        # Optional additional affine squeeze after MEC in later iterations
        if (it % 6) == 0:
            _, _, dmax_prev = farthest_pair_indices(final)
            gamma_polish2 = 0.006 * (1.0 - 0.5 * (it / 60.0))
            final_aff2, ok_aff2 = affine_major_axis_squeeze(final, gamma=gamma_polish2, tol_rel=5e-4, trust=0.02, max_backtrack=5, prev_dmax=dmax_prev)
            if ok_aff2:
                final = final_aff2
        final = project_feasible(final, max_passes=3)

    best_points = final.copy()
    # Compute final ratio^2 (D_min normalized to 1)
    D = pairwise_distances(best_points)
    dmin = min_nonzero_distance(D)
    dmax = max_distance(D)
    ratio_sq = float((dmax / dmin) ** 2)
    return best_points, ratio_sq
# EVOLVE_END


if __name__ == "__main__":
    points, _ = optimize_construct(16, 2)
    print(json.dumps({"points": points.tolist()}))
```
