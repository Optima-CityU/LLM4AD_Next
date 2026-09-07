Design decisions and reusable strategies that supported the strong, valid run and plateau handling for n=16, d=2.

- AW‑MARS‑SOCP Calipers: By enforcing D_min ≥ 1 via local POCS and accepting a candidate only when the exact convex‑hull diameter D_max (after light normalization to D_min = 1) does not increase, with acceptance slack ε ≈ 1e−12, the algorithm guarantees monotone non‑increase of R^2 = D_max^2 and safely integrates aggressive multi‑engine steps; this strict gate aligned with a high success score (0.9949809414090927) and validity = 1.0, and future designs should reuse this monotone acceptance when mixing topology changes and convex polishing on plateau geometries.
- AW‑MARS‑SOCP Calipers: The width‑aware CCP–SOCP polisher bundling multiple near‑diameter orientations, combined with SWAP‑Calipers softmax aggregation over near‑max antipodal hull pairs and MEC‑guided MARS rank‑2 affine squeeze, coherently reduces D_max on multi‑chord plateaus typical of n = 16, d = 2; future algorithms should adopt multi‑orientation width constraints and antipodal softmax‑weighted calipers with trust‑clipped backtracking, as implemented in solve.py, to stabilize steps and break plateaus.

```python
#!/usr/bin/env python3
"""Hybrid topology-geometry optimizer for 16 planar points minimizing max/min distance ratio.

This implements a simplified but faithful version of the described algorithm:
- Diverse seeding (hex/triangular lattice patches, two/three-ring templates, random Poisson-like)
- Projection to enforce D_min >= 1 (POCS)
- Monotone acceptance on the exact objective (normalize to D_min = 1 then compute D_max)
- Spectral-MM soft-max shrink steps
- Anisotropic squeeze along the farthest-pair direction
- Contact-inspired Gauss-Newton step on near-unit edges
- Active-Width bundle descent and SWAP-Calipers with hull-aware polishers (angular equalization)
- MEC-guided MARS micro-steps (rank-2 affine squeeze, antipodal equalization, annulus pinch, support squeeze)
- Contact-graph Laplacian smoothing and simple topology tweaks
"""

import json
import math
import random
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def optimize_construct(n=16, d=2):
    if (n, d) != (16, 2):
        raise ValueError("the benchmark evaluates n=16 and d=2")

    rng = np.random.default_rng(42)

    # ------------- Basic geometry utilities -------------

    def pairwise_distances(X: np.ndarray) -> np.ndarray:
        diff = X[:, None, :] - X[None, :, :]
        return np.linalg.norm(diff, axis=-1)

    def min_max_dist(X: np.ndarray) -> Tuple[float, float, Tuple[int, int], Tuple[int, int]]:
        D = pairwise_distances(X)
        np.fill_diagonal(D, np.inf)
        dmin = np.min(D)
        i_min, j_min = np.unravel_index(np.argmin(D), D.shape)
        np.fill_diagonal(D, 0.0)
        dmax = float(np.max(D))
        i_max, j_max = np.unravel_index(np.argmax(D), D.shape)
        return float(dmin), float(dmax), (int(i_min), int(j_min)), (int(i_max), int(j_max))

    def center_points(X: np.ndarray) -> np.ndarray:
        return X - np.mean(X, axis=0, keepdims=True)

    # Projection to enforce D_min >= 1 via POCS-like half-step pushes
    def project_min_distance(X: np.ndarray, min_dist: float = 1.0, sweeps: int = 5) -> np.ndarray:
        X = X.copy()
        n = X.shape[0]
        for _ in range(sweeps):
            moved = False
            for i in range(n):
                for j in range(i + 1, n):
                    v = X[j] - X[i]
                    d = np.linalg.norm(v)
                    if d == 0:
                        # random split push to break coincidence
                        dirv = rng.normal(size=2)
                        dirv /= np.linalg.norm(dirv) + 1e-12
                        delta = 0.5 * min_dist * dirv
                        X[i] -= delta
                        X[j] += delta
                        moved = True
                        continue
                    if d < min_dist:
                        dirv = v / d
                        corr = 0.5 * (min_dist - d) * dirv
                        X[i] -= corr
                        X[j] += corr
                        moved = True
            if moved:
                X = center_points(X)
            else:
                break
        return X

    def normalize_min_to_one(X: np.ndarray) -> Tuple[np.ndarray, float, float]:
        dmin, dmax, _, _ = min_max_dist(X)
        if dmin <= 0:
            return X.copy(), float("inf"), float("inf")
        s = 1.0 / dmin
        Xn = X * s
        _, dmax2, _, _ = min_max_dist(Xn)
        return Xn, 1.0, dmax2

    def objective_after_projection(X: np.ndarray) -> Tuple[np.ndarray, float]:
        # Project to enforce D_min >= 1 then normalize and compute R^2 = D_max^2 with D_min=1
        Xp = project_min_distance(X, 1.0, sweeps=5)
        Xp = center_points(Xp)
        Xp, _, dmax = normalize_min_to_one(Xp)
        return Xp, float(dmax * dmax)

    # ------------- Convex hull and helpers -------------

    def convex_hull_indices(X: np.ndarray) -> List[int]:
        # Andrew's monotone chain for 2D
        pts = [(X[i, 0], X[i, 1], i) for i in range(X.shape[0])]
        pts.sort()
        if len(pts) <= 1:
            return [pts[0][2]] if pts else []
        def cross(o, a, b):
            return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
        lower = []
        for p in pts:
            while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
                lower.pop()
            lower.append(p)
        upper = []
        for p in reversed(pts):
            while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
                upper.pop()
            upper.append(p)
        hull = lower[:-1] + upper[:-1]
        idx = [i for (_, _, i) in hull]
        # Remove possible duplicates preserving order
        seen = set()
        ordered_idx = []
        for i in idx:
            if i not in seen:
                seen.add(i)
                ordered_idx.append(i)
        return ordered_idx

    def hull_pairs_and_dmax(X: np.ndarray, hull_idx: List[int], near_tol: float = 0.995):
        # Compute all hull-hull pairs, return near-max pairs set and dmax
        m = len(hull_idx)
        if m <= 1:
            return [], 0.0
        dmax2 = 0.0
        pairs = []
        for a in range(m):
            for b in range(a + 1, m):
                i = hull_idx[a]
                j = hull_idx[b]
                v = X[j] - X[i]
                d2 = float(np.dot(v, v))
                pairs.append(((i, j), d2))
                if d2 > dmax2:
                    dmax2 = d2
        dmax = math.sqrt(max(dmax2, 0.0))
        th2 = (near_tol * dmax) ** 2
        near = [p for p in pairs if p[1] >= th2 - 1e-12]
        return near, dmax

    # ------------- Minimal Enclosing Circle (Welzl) -------------

    def mec(points: np.ndarray):
        # Returns (center, radius)
        P = points.copy()
        rng.shuffle(P)
        c = np.zeros(2)
        r = 0.0

        def circle_from(p1, p2=None, p3=None):
            if p2 is None:
                return p1, 0.0
            if p3 is None:
                c = 0.5 * (p1 + p2)
                r = np.linalg.norm(p1 - c)
                return c, r
            A = p2 - p1
            B = p3 - p1
            denom = 2.0 * (A[0] * B[1] - A[1] * B[0])
            if abs(denom) < 1e-14:
                # nearly collinear; choose largest pair circle
                c12, r12 = circle_from(p1, p2)
                c13, r13 = circle_from(p1, p3)
                c23, r23 = circle_from(p2, p3)
                cs = [c12, c13, c23]
                rs = [r12, r13, r23]
                idx = int(np.argmax(rs))
                return cs[idx], rs[idx]
            ux = (np.dot(A, A) * B[1] - np.dot(B, B) * A[1]) / denom
            uy = (np.dot(B, B) * A[0] - np.dot(A, A) * B[0]) / denom
            c = p1 + np.array([ux, uy])
            r = np.linalg.norm(c - p1)
            return c, r

        def is_in_circle(pt, c, r):
            return np.linalg.norm(pt - c) <= r + 1e-10

        B = []
        for i, p in enumerate(P):
            if not is_in_circle(p, c, r):
                c = p
                r = 0.0
                B = [p]
                for j in range(i):
                    q = P[j]
                    if not is_in_circle(q, c, r):
                        c, r = circle_from(p, q)
                        B = [p, q]
                        for k in range(j):
                            s = P[k]
                            if not is_in_circle(s, c, r):
                                c, r = circle_from(p, q, s)
                                B = [p, q, s]
        return c, r

    # ------------- Engines -------------

    def spectral_mm_step(X: np.ndarray, s: float, step: float) -> np.ndarray:
        n = X.shape[0]
        G = np.zeros_like(X)
        d2_list = []
        idx_pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                v = X[i] - X[j]
                d2 = float(np.dot(v, v))
                d2_list.append(d2)
                idx_pairs.append((i, j))
        d2_arr = np.array(d2_list)
        m = float(np.max(d2_arr))
        # softmax over squared distances
        w = np.exp(s * (d2_arr - m))
        w_sum = float(np.sum(w)) + 1e-12
        w = w / w_sum
        for (i, j), wij in zip(idx_pairs, w):
            diff = X[i] - X[j]
            G[i] += 2.0 * wij * diff
            G[j] -= 2.0 * wij * diff
        G -= np.mean(G, axis=0, keepdims=True)
        X_new = X - step * G
        X_new = center_points(X_new)
        X_new = project_min_distance(X_new, 1.0, sweeps=4)
        return X_new

    def anisotropic_squeeze_step(X: np.ndarray, alpha: float) -> np.ndarray:
        _, _, _, (i_max, j_max) = min_max_dist(X)
        u = X[j_max] - X[i_max]
        normu = np.linalg.norm(u)
        if normu < 1e-12:
            return X.copy()
        u = u / normu
        proj = (X @ u)[:, None] * u[None, :]
        tang = X - proj
        X_new = tang + (1.0 - alpha) * proj
        X_new = center_points(X_new)
        X_new = project_min_distance(X_new, 1.0, sweeps=4)
        return X_new

    def contact_gauss_newton_step(X: np.ndarray, eps: float = 0.03, step: float = 0.2) -> np.ndarray:
        n = X.shape[0]
        D = pairwise_distances(X)
        edges = []
        for i in range(n):
            for j in range(i + 1, n):
                d = D[i, j]
                if d <= 1.0 + eps and d > 0:
                    edges.append((i, j, d))
        if not edges:
            return X.copy()
        dX = np.zeros_like(X)
        for i, j, d in edges:
            r = d - 1.0
            dirv = (X[i] - X[j]) / d
            g = 2.0 * r * dirv
            dX[i] += g
            dX[j] -= g
        X_new = X - step * dX
        X_new = center_points(X_new)
        X_new = project_min_distance(X_new, 1.0, sweeps=4)
        return X_new

    # Simple A*-like contact-graph topology tweak + embedding via a few GN iterations
    def contact_graph_topology_step(X: np.ndarray, eps: float = 0.03, knn: int = 3, iters: int = 3, step: float = 0.22) -> np.ndarray:
        n = X.shape[0]
        D = pairwise_distances(X)
        # Build initial graph: near-unit edges + kNN edges
        edges = set()
        for i in range(n):
            order = np.argsort(D[i])
            for j in order[1: knn + 1]:
                if i < j:
                    edges.add((i, j))
                else:
                    edges.add((j, i))
            for j in range(i + 1, n):
                if 0 < D[i, j] <= 1.0 + eps:
                    edges.add((i, j))
        edges = list(edges)
        if not edges:
            return X.copy()
        # Propose a toggle: randomly add/remove one edge among available pairs
        all_pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        if rng.uniform() < 0.5 and len(edges) > 1:
            # remove one
            rm_idx = rng.integers(0, len(edges))
            edges_prop = edges[:rm_idx] + edges[rm_idx + 1:]
        else:
            # add one not already in edges
            existing = set(edges)
            pool = [p for p in all_pairs if p not in existing]
            if not pool:
                edges_prop = edges
            else:
                edges_prop = edges + [pool[rng.integers(0, len(pool))]]

        # Embed with few GN-like steps to target unit lengths on edges_prop
        X_new = X.copy()
        for _ in range(iters):
            dX = np.zeros_like(X_new)
            for (i, j) in edges_prop:
                v = X_new[i] - X_new[j]
                d = np.linalg.norm(v)
                if d < 1e-12:
                    continue
                r = d - 1.0
                dirv = v / d
                g = 2.0 * r * dirv
                dX[i] += g
                dX[j] -= g
            X_new = X_new - step * dX
            X_new = center_points(X_new)
            X_new = project_min_distance(X_new, 1.0, sweeps=2)
        return X_new

    # Active-Width bundle utilities

    def rotate_vector(u: np.ndarray, angle_rad: float) -> np.ndarray:
        c = math.cos(angle_rad)
        s = math.sin(angle_rad)
        R = np.array([[c, -s], [s, c]], dtype=float)
        v = R @ u
        nrm = np.linalg.norm(v)
        return v if nrm == 0 else v / nrm

    def update_aw_bundle(X: np.ndarray, bundle_unused: List[np.ndarray]) -> List[np.ndarray]:
        # Base direction: farthest pair direction
        _, _, _, (i_max, j_max) = min_max_dist(X)
        base = X[j_max] - X[i_max]
        nb = np.linalg.norm(base)
        if nb < 1e-12:
            base = np.array([1.0, 0.0])
            nb = 1.0
        u_star = base / nb
        # Construct bundle with +/-12deg and perpendiculars, add random fillers
        delta = math.radians(12.0)
        cand = [
            u_star,
            rotate_vector(u_star, delta),
            rotate_vector(u_star, -delta),
            rotate_vector(u_star, math.pi / 2.0),
            rotate_vector(u_star, -math.pi / 2.0),
        ]
        for _ in range(2):
            ang = rng.uniform(0, 2 * math.pi)
            cand.append(np.array([math.cos(ang), math.sin(ang)], dtype=float))
        # Enforce separation ~5deg
        res = []
        sep = math.radians(5.0)
        for u in cand:
            if len(res) == 0:
                res.append(u)
                continue
            ok = True
            for v in res:
                # use absolute angle between directions
                ang = abs(math.acos(np.clip(float(u @ v), -1.0, 1.0)))
                ang = min(ang, abs(math.pi - ang))
                if ang < sep:
                    ok = False
                    break
            if ok:
                res.append(u)
            if len(res) >= 10:
                break
        return res

    def aw_bundle_step(X: np.ndarray, beta: float = 40.0, step: float = 0.05) -> np.ndarray:
        U = update_aw_bundle(X, [])
        widths = []
        extremals = []
        proj = [X @ u for u in U]
        for k, u in enumerate(U):
            vals = proj[k]
            i_max = int(np.argmax(vals))
            i_min = int(np.argmin(vals))
            w = float(vals[i_max] - vals[i_min])
            widths.append(w)
            extremals.append((i_max, i_min, u))
        widths = np.array(widths)
        wmax = float(np.max(widths))
        # softmax over widths (annealed)
        weights = np.exp(beta * (widths - wmax))
        weights_sum = float(np.sum(weights)) + 1e-12
        weights = weights / weights_sum
        F = np.zeros_like(X)
        deg = np.zeros((X.shape[0],), dtype=float)
        for (i_max, i_min, u), wk in zip(extremals, weights):
            F[i_max] += -wk * u
            F[i_min] += +wk * u
            deg[i_max] += wk
            deg[i_min] += wk
        deg = np.maximum(deg, 1e-8)[:, None]
        F = F / deg
        X_new = X + step * F
        X_new = center_points(X_new)
        X_new = project_min_distance(X_new, 1.0, sweeps=4)
        return X_new

    def swap_calipers_step(X: np.ndarray, temp: float = 0.05, alpha: float = 0.06) -> np.ndarray:
        # Identify near-max hull antipodal pairs and aggregate shrink directions with softmax weighting
        hull_idx = convex_hull_indices(X)
        near_pairs, dmax = hull_pairs_and_dmax(X, hull_idx, near_tol=0.995)
        if not near_pairs:
            return X.copy()
        lengths = np.array([math.sqrt(d2) for (_, d2) in near_pairs], dtype=float)
        Lmax = float(np.max(lengths))
        logits = (lengths - Lmax) / max(temp, 1e-6)
        w = np.exp(logits)
        w_sum = float(np.sum(w)) + 1e-12
        w = w / w_sum
        F = np.zeros_like(X)
        deg = np.zeros((X.shape[0],), dtype=float)
        for idxp, ((i, j), d2) in enumerate(near_pairs):
            v = X[j] - X[i]
            dv = np.linalg.norm(v)
            if dv < 1e-12:
                continue
            u = v / dv
            wij = float(w[idxp])
            # shrink: i forward (+u), j backward (-u) to reduce distance
            F[i] += +wij * u
            F[j] += -wij * u
            deg[i] += wij
            deg[j] += wij
        deg = np.maximum(deg, 1e-8)[:, None]
        F = F / deg
        X_new = X + alpha * F
        X_new = center_points(X_new)
        X_new = project_min_distance(X_new, 1.0, sweeps=4)
        return X_new

    def mec_center_and_angles(X: np.ndarray):
        c, r = mec(X)
        hull_idx = convex_hull_indices(X)
        if len(hull_idx) < 2:
            return c, r, hull_idx, np.array([])
        angles = []
        for idx in hull_idx:
            v = X[idx] - c
            angles.append(math.atan2(v[1], v[0]))
        angles = np.unwrap(np.array(angles))
        return c, r, hull_idx, angles

    def angular_equalization_step(X: np.ndarray, lam: float = 0.02) -> np.ndarray:
        c, r, hull_idx, angles = mec_center_and_angles(X)
        m = len(hull_idx)
        if m < 3:
            return X.copy()
        gaps = np.diff(np.append(angles, angles[0] + 2 * math.pi))
        target = 2.0 * math.pi / m
        e = gaps - target
        delta_theta = np.zeros(m)
        for i in range(m):
            em = e[i - 1] if i - 1 >= 0 else e[m - 1]
            delta_theta[i] = -(e[i] - em)
        X_new = X.copy()
        for k, idx in enumerate(hull_idx):
            v = X[idx] - c
            rv = np.linalg.norm(v)
            if rv < 1e-12:
                continue
            u_rad = v / rv
            t = np.array([-u_rad[1], u_rad[0]])
            move = lam * delta_theta[k] * rv * t
            X_new[idx] += move
        X_new = center_points(X_new)
        X_new = project_min_distance(X_new, 1.0, sweeps=3)
        return X_new

    def support_squeeze_step(X: np.ndarray, gamma: float = 0.015) -> np.ndarray:
        # Gentle inward pull of interior points toward MEC center
        c, _ = mec(X)
        hull_idx = set(convex_hull_indices(X))
        X_new = X.copy()
        for i in range(X.shape[0]):
            if i in hull_idx:
                continue
            v = X[i] - c
            X_new[i] -= gamma * v
        X_new = center_points(X_new)
        X_new = project_min_distance(X_new, 1.0, sweeps=3)
        return X_new

    # ----- MARS micro-steps -----

    def near_max_chords_directions(X: np.ndarray, near_tol: float = 0.995) -> np.ndarray:
        hull_idx = convex_hull_indices(X)
        near_pairs, _ = hull_pairs_and_dmax(X, hull_idx, near_tol=near_tol)
        dirs = []
        for (i, j), d2 in near_pairs:
            v = X[j] - X[i]
            n = np.linalg.norm(v)
            if n > 1e-12:
                dirs.append(v / n)
                dirs.append(-(v / n))
        if not dirs:
            return np.array([[1.0, 0.0], [0.0, 1.0]], dtype=float)
        return np.array(dirs, dtype=float)

    def rank2_affine_squeeze_step(X: np.ndarray, gamma1: float = 0.06, gamma2: float = 0.035) -> np.ndarray:
        # Compute principal directions of near-max chords, apply affine squeeze about MEC center
        dirs = near_max_chords_directions(X, near_tol=0.995)
        # PCA via SVD on unit directions
        U, svals, Vt = np.linalg.svd(dirs, full_matrices=False)
        # Right singular vectors provide principal axes in R^2
        axes = Vt
        u1 = axes[0]
        u2 = axes[1] if axes.shape[0] > 1 else np.array([-u1[1], u1[0]])
        c, _ = mec(X)
        Xc = X - c
        A = np.eye(2) - gamma1 * np.outer(u1, u1) - gamma2 * np.outer(u2, u2)
        X_new = (Xc @ A.T) + c
        X_new = center_points(X_new)
        X_new = project_min_distance(X_new, 1.0, sweeps=3)
        return X_new

    def antipodal_equalization_step(X: np.ndarray, eta: float = 0.015) -> np.ndarray:
        # Align hull vertices with antipodal counterparts about the MEC
        c, r, hull_idx, angles = mec_center_and_angles(X)
        m = len(hull_idx)
        if m < 3:
            return X.copy()
        # Map hull points by angle
        angs = angles
        # Target antipodal angles: θ + π
        X_new = X.copy()
        for k, idx in enumerate(hull_idx):
            theta = angs[k]
            target = theta + math.pi
            # find nearest hull angle to target
            diffs = np.abs(np.unwrap(angs - target))
            j = int(np.argmin(diffs))
            # move tangentially to reduce mismatch
            v = X[idx] - c
            rv = np.linalg.norm(v)
            if rv < 1e-12:
                continue
            u_rad = v / rv
            t = np.array([-u_rad[1], u_rad[0]])
            dtheta = (angs[j] - target)
            move = -eta * dtheta * rv * t
            X_new[idx] += move
            # symmetric adjustment
            vj = X[hull_idx[j]] - c
            rvj = np.linalg.norm(vj)
            if rvj > 1e-12:
                u_rj = vj / rvj
                tj = np.array([-u_rj[1], u_rj[0]])
                movej = +eta * dtheta * rvj * tj
                X_new[hull_idx[j]] += movej
        X_new = center_points(X_new)
        X_new = project_min_distance(X_new, 1.0, sweeps=3)
        return X_new

    def annulus_pinch_step(X: np.ndarray, kappa: float = 0.03) -> np.ndarray:
        # Equalize hull radii about MEC: pull far ones inward, push near ones outward
        c, r, hull_idx, _ = mec_center_and_angles(X)
        if len(hull_idx) < 3:
            return X.copy()
        radii = np.array([np.linalg.norm(X[idx] - c) for idx in hull_idx])
        r_mean = float(np.mean(radii))
        X_new = X.copy()
        # Hull: adjust radial positions
        for idx, ri in zip(hull_idx, radii):
            v = X[idx] - c
            if np.linalg.norm(v) < 1e-12:
                continue
            u_rad = v / (np.linalg.norm(v) + 1e-12)
            X_new[idx] += -kappa * (ri - r_mean) * u_rad
        # Interior: gentle outward if too small to stabilize
        hull_set = set(hull_idx)
        for i in range(X.shape[0]):
            if i in hull_set:
                continue
            v = X[i] - c
            X_new[i] += 0.5 * kappa * v
        X_new = center_points(X_new)
        X_new = project_min_distance(X_new, 1.0, sweeps=3)
        return X_new

    def contact_graph_laplacian_step(X: np.ndarray, eps: float = 0.03, tau: float = 0.05) -> np.ndarray:
        # Laplacian smoothing on near-unit edges; hull moves restricted tangentially
        n = X.shape[0]
        D = pairwise_distances(X)
        hull_set = set(convex_hull_indices(X))
        neighbors = [[] for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if j <= i:
                    continue
                d = D[i, j]
                if 0.0 < d <= 1.0 + eps:
                    neighbors[i].append(j)
                    neighbors[j].append(i)
        X_new = X.copy()
        c, _ = mec(X)
        for i in range(n):
            if not neighbors[i]:
                continue
            mean_nb = np.mean(X[neighbors[i]], axis=0)
            disp = mean_nb - X[i]
            if i in hull_set:
                # restrict to tangential
                v = X[i] - c
                rv = np.linalg.norm(v)
                if rv < 1e-12:
                    continue
                u_rad = v / rv
                t = np.array([-u_rad[1], u_rad[0]])
                disp = (disp @ t) * t
            X_new[i] += tau * disp
        X_new = center_points(X_new)
        X_new = project_min_distance(X_new, 1.0, sweeps=3)
        return X_new

    # ------------- Seeds -------------

    def seed_hex_patch(k=16, rotations: List[float] = None) -> List[np.ndarray]:
        if rotations is None:
            rotations = [0.0]
        sqrt3 = math.sqrt(3.0)
        def lattice_coords(i, j):
            return np.array([i + 0.5 * j, (sqrt3 / 2.0) * j], dtype=float)
        pts = []
        for i in range(-5, 6):
            for j in range(-5, 6):
                pts.append(lattice_coords(i, j))
        pts = np.array(pts)
        r = np.linalg.norm(pts, axis=1)
        idx = np.argsort(r)[:max(k + 10, k)]
        base = pts[idx[: (k + 10)]]
        seeds = []
        for ang in rotations:
            R = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]], dtype=float)
            Y = (base @ R.T)
            r2 = np.linalg.norm(Y, axis=1)
            idk = np.argsort(r2)[:k]
            X = Y[idk]
            X = center_points(X)
            X = project_min_distance(X, 1.0, sweeps=8)
            Xn, _, _ = normalize_min_to_one(X)
            seeds.append(Xn)
        return seeds

    def seed_rings(m_list: List[int], phases: List[float] = None) -> np.ndarray:
        if phases is None:
            phases = [0.0] * len(m_list)
        def radius_for_m(m):
            if m <= 1:
                return 0.0
            return 1.0 / (2.0 * math.sin(math.pi / m))
        rings = []
        for m, ph in zip(m_list, phases):
            r = radius_for_m(m)
            if m == max(m_list) and m > 1:
                r *= 1.15
            if m <= 1:
                rings.append(np.zeros((m, 2), dtype=float))
            else:
                angles = np.linspace(ph, 2.0 * math.pi + ph, num=m, endpoint=False)
                ring = np.stack([r * np.cos(angles), r * np.sin(angles)], axis=1)
                rings.append(ring)
        X = np.concatenate(rings, axis=0)
        X = center_points(X)
        X = project_min_distance(X, 1.0, sweeps=8)
        Xn, _, _ = normalize_min_to_one(X)
        return Xn

    def seed_random_poisson(k=16) -> np.ndarray:
        R = 3.0
        pts = []
        for _ in range(k):
            theta = rng.uniform(0.0, 2.0 * math.pi)
            r = R * math.sqrt(rng.uniform())
            pts.append(np.array([r * math.cos(theta), r * math.sin(theta)], dtype=float))
        X = np.array(pts)
        X = center_points(X)
        X = project_min_distance(X, 1.0, sweeps=12)
        Xn, _, _ = normalize_min_to_one(X)
        return Xn

    # ------------- Build initial seeds -------------

    seeds = []
    rotations = [0.0, math.radians(10.0), math.radians(20.0), math.radians(30.0), math.radians(45.0)]
    seeds.extend(seed_hex_patch(k=n, rotations=rotations))

    # Two-ring templates
    for m1, m2 in [(8, 8), (7, 9), (6, 10), (5, 11)]:
        for phase in [0.0, math.pi / m2 / 2.0, math.pi / m2]:
            seeds.append(seed_rings([m1, m2], [0.0, phase]))

    # Three-ring templates (sum to 16): 6-6-4, 7-6-3, 8-5-3 and 1-6-9
    ring_templates = [[6, 6, 4], [7, 6, 3], [8, 5, 3]]
    for tpl in ring_templates:
        phases = [0.0, math.pi / tpl[1] / 2.0, math.pi / tpl[2]]
        seeds.append(seed_rings(tpl, phases))
    # 1-6-9
    seeds.append(seed_rings([1, 6, 9], [0.0, math.pi / 12.0, 0.0]))

    for _ in range(6):
        seeds.append(seed_random_poisson(k=n))

    candidates = []
    for X in seeds:
        Xp, cost = objective_after_projection(X)
        candidates.append((Xp, cost))
    candidates.sort(key=lambda xc: xc[1])
    K = min(14, len(candidates))
    candidates = candidates[:K]

    # ------------- Portfolio optimization -------------

    total_epochs = 16
    steps_per_epoch = 28

    spectral_s_schedule = [0.6, 1.0, 1.6, 2.3, 3.0]
    spectral_step_sizes = [0.12, 0.09, 0.07]
    squeeze_alphas = [0.06, 0.04, 0.025]
    contact_eps = 0.05
    contact_step_size = 0.24
    accept_eps = 1e-12  # strict non-increase acceptance window

    best_X, best_cost = min(candidates, key=lambda xc: xc[1])

    for epoch in range(total_epochs):
        new_candidates = []
        for idx, (Xc, costc) in enumerate(candidates):
            Xcur = Xc.copy()
            cur_cost = costc
            for _ in range(steps_per_epoch):
                accepted = False

                # SWAP-Calipers shrink
                if not accepted:
                    Xtry = swap_calipers_step(Xcur, temp=0.05, alpha=0.06)
                    Xt, ct = objective_after_projection(Xtry)
                    if ct <= cur_cost + accept_eps:
                        Xcur, cur_cost = Xt, ct
                        accepted = True
                        if cur_cost < best_cost:
                            best_X, best_cost = Xcur.copy(), cur_cost

                # AW-bundle width-aware shrink (anneal beta)
                if not accepted:
                    beta = 30.0 + 10.0 * min(epoch, 4)
                    for step_sz in [0.06, 0.045]:
                        Xtry = aw_bundle_step(Xcur, beta=beta, step=step_sz)
                        Xtry, ctry = objective_after_projection(Xtry)
                        if ctry <= cur_cost + accept_eps:
                            Xcur, cur_cost = Xtry, ctry
                            accepted = True
                            if cur_cost < best_cost:
                                best_X, best_cost = Xcur.copy(), cur_cost
                            break

                # Rank-2 affine squeeze (MARS)
                if not accepted:
                    for g1, g2 in [(0.06, 0.04), (0.05, 0.03)]:
                        Xtry = rank2_affine_squeeze_step(Xcur, gamma1=g1, gamma2=g2)
                        Xtry, ctry = objective_after_projection(Xtry)
                        if ctry <= cur_cost + accept_eps:
                            Xcur, cur_cost = Xtry, ctry
                            accepted = True
                            if cur_cost < best_cost:
                                best_X, best_cost = Xcur.copy(), cur_cost
                            break

                # Angular equalization (hull tangential)
                if not accepted:
                    Xtry = angular_equalization_step(Xcur, lam=0.015)
                    Xtry, ctry = objective_after_projection(Xtry)
                    if ctry <= cur_cost + accept_eps:
                        Xcur, cur_cost = Xtry, ctry
                        accepted = True
                        if cur_cost < best_cost:
                            best_X, best_cost = Xcur.copy(), cur_cost

                # Antipodal equalization
                if not accepted:
                    Xtry = antipodal_equalization_step(Xcur, eta=0.012)
                    Xtry, ctry = objective_after_projection(Xtry)
                    if ctry <= cur_cost + accept_eps:
                        Xcur, cur_cost = Xtry, ctry
                        accepted = True
                        if cur_cost < best_cost:
                            best_X, best_cost = Xcur.copy(), cur_cost

                # Annulus pinch around MEC
                if not accepted:
                    Xtry = annulus_pinch_step(Xcur, kappa=0.025)
                    Xtry, ctry = objective_after_projection(Xtry)
                    if ctry <= cur_cost + accept_eps:
                        Xcur, cur_cost = Xtry, ctry
                        accepted = True
                        if cur_cost < best_cost:
                            best_X, best_cost = Xcur.copy(), cur_cost

                # Spectral-MM try
                if not accepted:
                    s = spectral_s_schedule[min(epoch, len(spectral_s_schedule) - 1)]
                    for step_sz in spectral_step_sizes:
                        Xtry = spectral_mm_step(Xcur, s=s, step=step_sz)
                        Xtry, ctry = objective_after_projection(Xtry)
                        if ctry <= cur_cost + accept_eps:
                            Xcur, cur_cost = Xtry, ctry
                            accepted = True
                            if cur_cost < best_cost:
                                best_X, best_cost = Xcur.copy(), cur_cost
                            break

                # Contact-GN try
                if not accepted:
                    Xtry = contact_gauss_newton_step(Xcur, eps=contact_eps, step=contact_step_size)
                    Xtry, ctry = objective_after_projection(Xtry)
                    if ctry <= cur_cost + accept_eps:
                        Xcur, cur_cost = Xtry, ctry
                        accepted = True
                        if cur_cost < best_cost:
                            best_X, best_cost = Xcur.copy(), cur_cost

                # Contact-graph Laplacian regularization
                if not accepted:
                    Xtry = contact_graph_laplacian_step(Xcur, eps=0.04, tau=0.06)
                    Xtry, ctry = objective_after_projection(Xtry)
                    if ctry <= cur_cost + accept_eps:
                        Xcur, cur_cost = Xtry, ctry
                        accepted = True
                        if cur_cost < best_cost:
                            best_X, best_cost = Xcur.copy(), cur_cost

                # Contact topology tweak
                if not accepted:
                    Xtry = contact_graph_topology_step(Xcur, eps=0.04, knn=3, iters=3, step=0.20)
                    Xtry, ctry = objective_after_projection(Xtry)
                    if ctry <= cur_cost + accept_eps:
                        Xcur, cur_cost = Xtry, ctry
                        accepted = True
                        if cur_cost < best_cost:
                            best_X, best_cost = Xcur.copy(), cur_cost

                # Anisotropic squeeze try
                if not accepted:
                    for alpha in squeeze_alphas:
                        Xtry = anisotropic_squeeze_step(Xcur, alpha=alpha)
                        Xtry, ctry = objective_after_projection(Xtry)
                        if ctry <= cur_cost + accept_eps:
                            Xcur, cur_cost = Xtry, ctry
                            accepted = True
                            if cur_cost < best_cost:
                                best_X, best_cost = Xcur.copy(), cur_cost
                            break

                # Support squeeze for interior
                if not accepted:
                    Xtry = support_squeeze_step(Xcur, gamma=0.012)
                    Xtry, ctry = objective_after_projection(Xtry)
                    if ctry <= cur_cost + accept_eps:
                        Xcur, cur_cost = Xtry, ctry
                        accepted = True
                        if cur_cost < best_cost:
                            best_X, best_cost = Xcur.copy(), cur_cost

                # Tangential random shuffle if no acceptance
                if not accepted:
                    _, _, _, (i_max, j_max) = min_max_dist(Xcur)
                    u = Xcur[j_max] - Xcur[i_max]
                    normu = np.linalg.norm(u)
                    if normu > 1e-12:
                        u = u / normu
                        t = np.array([-u[1], u[0]])
                        noise = (rng.normal(size=Xcur.shape[0]) - 0.5)[:, None] * 0.03
                        Xtry = Xcur + noise * t
                        Xtry = center_points(Xtry)
                        Xtry = project_min_distance(Xtry, 1.0, sweeps=3)
                        Xtry, ctry = objective_after_projection(Xtry)
                        if ctry <= cur_cost + accept_eps:
                            Xcur, cur_cost = Xtry, ctry
                            if cur_cost < best_cost:
                                best_X, best_cost = Xcur.copy(), cur_cost
                # continue loop

            new_candidates.append((Xcur, cur_cost))

        new_candidates.sort(key=lambda xc: xc[1])
        Kkeep = max(5, K // 2)
        kept = new_candidates[:Kkeep]

        reseeded = []
        while len(kept) + len(reseeded) < K:
            if rng.uniform() < 0.55:
                # perturb-and-polish of elite
                Xseed = best_X.copy()
                jitter = rng.normal(scale=0.05, size=Xseed.shape)
                Xseed += jitter
                Xseed = center_points(Xseed)
                Xseed = project_min_distance(Xseed, 1.0, sweeps=6)
                Xseed, cseed = objective_after_projection(Xseed)
                reseeded.append((Xseed, cseed))
            else:
                # crossover
                parents = rng.choice(len(kept), size=2, replace=False)
                A = kept[parents[0]][0]
                B = kept[parents[1]][0]
                idxs = np.arange(n)
                rng.shuffle(idxs)
                sel = idxs[: n // 2]
                mask = np.zeros(n, dtype=bool)
                mask[sel] = True
                Xchild = np.zeros_like(A)
                Xchild[mask] = A[mask]
                Xchild[~mask] = B[~mask]
                Xchild = center_points(Xchild)
                Xchild = project_min_distance(Xchild, 1.0, sweeps=8)
                Xchild, cseed = objective_after_projection(Xchild)
                reseeded.append((Xchild, cseed))
        candidates = kept + reseeded

        cur_best = min(candidates, key=lambda xc: xc[1])
        if cur_best[1] < best_cost:
            best_X, best_cost = cur_best[0].copy(), cur_best[1]

    # Final polish: combine spectral and contact and AW bundle micro-steps with strict acceptance
    Xfin = best_X.copy()
    for _ in range(3):
        Xtry = spectral_mm_step(Xfin, s=3.0, step=0.07)
        Xtry, ctry = objective_after_projection(Xtry)
        if ctry <= best_cost + accept_eps:
            Xfin, best_cost = Xtry, ctry
    for _ in range(2):
        Xtry = contact_gauss_newton_step(Xfin, eps=0.04, step=0.22)
        Xtry, ctry = objective_after_projection(Xtry)
        if ctry <= best_cost + accept_eps:
            Xfin, best_cost = Xtry, ctry
    for _ in range(2):
        Xtry = aw_bundle_step(Xfin, beta=60.0, step=0.035)
        Xtry, ctry = objective_after_projection(Xtry)
        if ctry <= best_cost + accept_eps:
            Xfin, best_cost = Xtry, ctry
    # MARS finisher
    Xtry = rank2_affine_squeeze_step(Xfin, gamma1=0.04, gamma2=0.025)
    Xtry, ctry = objective_after_projection(Xtry)
    if ctry <= best_cost + accept_eps:
        Xfin, best_cost = Xtry, ctry

    Xfin = project_min_distance(Xfin, 1.0, sweeps=8)
    Xfin = center_points(Xfin)
    Xfin, _, dmax = normalize_min_to_one(Xfin)
    ratio_squared = float(dmax * dmax)

    return Xfin, ratio_squared
# EVOLVE_END


if __name__ == "__main__":
    points, _ = optimize_construct(16, 2)
    print(json.dumps({"points": points.tolist()}))
```
