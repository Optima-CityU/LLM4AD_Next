Feasibility-projection with D_min normalization, smooth surrogate annealing, and hex/ring-structured multi-island seeding stabilized contraction of D_max and maintained diversity, yielding a valid configuration with strong metrics.

- Hex-Island CMA-Projection for Minimal-Diameter Unit-Separation Packings: By normalizing the minimum pairwise distance to 1 and projecting any violating pairs back to exactly distance 1 after each step, the algorithm could contract the diameter aggressively without feasibility drift; combined with softmax/softmin annealing to transition from a smooth to sharp objective, this stabilized gradient steps and tightened the diameter. Seeding with unit-spaced hexagonal lattice patches and running multiple islands—including a CMA-ES ring-parameterization island—preserved structural diversity and enabled exploration of distinct topologies beyond a single hex patch, producing a valid result with ratio_squared 12.948560030844753, validity 1.0, and eval_time about 1.093 s. Future designs for max/min distance ratios should reuse this feasibility projection plus surrogate annealing and structurally informed seeding to balance stable contraction with diverse search under hard separation constraints.
- Caliper-Softmax HexRing Projection Islands (CSH-RPI): CSH-RPI couples an annealed softmax surrogate of pairwise distances (β≈20→80) for smooth early guidance with a calipers-driven, line-searched shrink that only accepts a step when the exact D_max after D_min=1 normalization does not increase, yielding stable progress on the true objective D_max^2. The hard projection-and-normalization repair after each move preserves feasibility by restoring all pairs to distance ≥1 and re-centering, which in this run coincided with validity=1.0 while allowing aggressive contractions. A multi-island mix of free-coordinate and ring-parameter islands (e.g., [1,6,9] patterns optimized by CMA/Nelder–Mead), combined with rotations/crossover and elite seeding from the enumerated 1+6+9 hex patch, sustained topology diversity and provided strong starting shapes that avoided plateaus. Reuse this two-stage loop—annealed softmax gradient steps followed by calipers-based line-searched diameter shrink under projection repair—and the ring-parameter island seeding to reliably drive down D_max^2 in ratio-minimization problems.
- Caliper–MEC HexRing Projection Islands (CM-HRPI): CM-HRPI enforces D_min ≥ 1 via local pairwise projection with gentle normalization and explicitly avoids unconditional global 1/D_min scaling, preventing blow-up and stabilizing contraction. It applies calipers-based active diameter shrink with backtracked line search that only accepts steps when the exact convex‑hull D_max does not increase, yielding monotone tightening of the true objective and reliable late-stage convergence (score 0.9984; ratio_squared 12.909641760794933; validity 1.0). Future designs should reuse this exact-objective acceptance combined with feasibility-preserving local projection and MEC/hull-guided inward pulls on hull points, while island migrations inject ring-parameterized topology moves to sustain diversity without sacrificing objective monotonicity.
- Caliper–Softmax Ring-Lattice Poly‑Islands with POCS and Contact Polish (CSRLP-POCS+CP): When minimizing R^2 with evaluation-time normalization D_min=1, the algorithm enforces feasibility using local pairwise POCS repairs (never global rescaling mid-move) and accepts a step only if rotating-calipers on the convex hull shows the post-POCS, normalized exact D_max does not increase under a backtracked line search, ensuring monotone behavior of the true objective D_max^2.
- Caliper–Softmax Ring-Lattice Poly‑Islands with POCS and Contact Polish (CSRLP-POCS+CP): A smooth early stage uses an annealed softmax surrogate over pairwise distances with a hinge barrier on dij<1 to produce coherent contractions, then switches to hull‑exact active-pair shrink with mild MEC/hull‑guided pulls on the outermost vertices to circularize the hull without breaking unit contacts.
- Caliper–Softmax Ring-Lattice Poly‑Islands with POCS and Contact Polish (CSRLP-POCS+CP): Ring‑parameter islands (e.g., two‑ring splits like 8–8, 6–10) explore low‑dimensional manifolds via micro‑CMA, periodically converting the best ring candidate to free coordinates for polishing by the exact calipers step, while an elite archive and cross‑island migrations maintain diversity.
- Caliper–Softmax Ring-Lattice Poly‑Islands with POCS and Contact Polish (CSRLP-POCS+CP): In this run the portfolio achieved ratio_squared=12.909265451498447 with validity=1.0 and a score of 0.9984507763378492, supporting the effectiveness of coupling annealed surrogate guidance with exact non‑worsening calipers acceptance under POCS feasibility.
- Exact-Calipers MEC Hybrid: Rank-2 Squeeze + Gap-Equalize + Antipodal Pivot Poly‑Islands: Use a strict acceptance gate that commits a move only when the exact convex-hull diameter D_max does not increase after POCS enforcement of D_min ≥ 1 and normalization, then apply rank‑2 anisotropic squeezes guided by PCA of the near‑max chord set to shorten multiple orientation classes of long chords while gap‑aware tangential equalization focuses updates on the top‑K largest angular gaps; when two or more near‑max antipodal pairs coexist, trigger small antipodal pivot swaps to break farthest‑pair lock‑in. This monotone, feasibility‑preserving portfolio as implemented in solve.py achieved validity 1.0 with ratio_squared 12.974554637255256 in 36.446603072981816 seconds (score 0.9934264776217938), indicating reliable convergence under the true objective; future designs should reuse the exact‑objective acceptance with POCS plus PCA‑guided rank‑2 squeezes and conditionally triggered pivot swaps to escape plateaus.

```python
#!/usr/bin/env python3
"""Optimizes 16 planar points to minimize (D_max / D_min)^2 using structured seeds
and projection-based contraction. The approach targets equal-circle-in-circle packing:
normalize D_min to 1 and minimize the diameter (D_max).

Core components:
- Hexagonal lattice (triangular grid) seeding with ring structure (1 + 6 + 9),
  where we choose the best 9 out of the 12 second-ring nodes by enumerating
  all 12 choose 9 subsets and selecting the configuration with the smallest
  diameter.
- Feasible contraction: repeatedly shrink the current diameter by moving the
  two farthest points toward each other, then projecting to enforce all pairwise
  distances >= 1, and finally rescaling to set min distance exactly 1.
- Additional seeds (random Poisson-disk-like and ring patterns) are used to
  maintain diversity and break local minima; all candidates undergo the same
  contraction pipeline and are compared.

The function optimize_construct returns:
- points: (n x 2) NumPy array of final coordinates
- ratio_squared: float value of (max distance / min distance)^2
"""

import json
import itertools
from math import sqrt, sin, pi
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
        # X: (m,2)
        diff = X[:, None, :] - X[None, :, :]
        D = np.linalg.norm(diff, axis=-1)
        return D

    def min_nonzero_distance(D):
        # D: pairwise distances with zeros on diagonal
        # Return minimum positive distance
        # Assume distinct points
        m = D.shape[0]
        # Mask diagonal
        mask = ~np.eye(m, dtype=bool)
        nonzero = D[mask]
        return np.min(nonzero)

    def max_distance(D):
        m = D.shape[0]
        mask = ~np.eye(m, dtype=bool)
        return np.max(D[mask])

    def centroid(X):
        return np.mean(X, axis=0)

    def recenter(X):
        c = centroid(X)
        X -= c
        return X

    def rescale_to_min_one(X, tol=1e-12):
        D = pairwise_distances(X)
        dmin = min_nonzero_distance(D)
        if dmin <= 0:
            return X  # degenerate, shouldn't happen
        # Scale so that min distance becomes exactly 1
        # If dmin is already ~1, avoid unnecessary scaling to mitigate noise
        if abs(dmin - 1.0) > tol:
            X *= (1.0 / dmin)
        return X

    def project_feasible(X, max_passes=5, tol=1e-12):
        # Ensure all pairwise distances >= 1 by pushing violating pairs apart
        # Iterate multiple passes because constraints couple.
        m = X.shape[0]
        for _ in range(max_passes):
            D = pairwise_distances(X)
            # Find violating pairs
            viol = np.argwhere(D < 1.0 - tol)
            if viol.size == 0:
                break
            # Randomize order to avoid systematic bias
            rng.shuffle(viol)
            for i, j in viol:
                if i >= j:
                    continue
                dij = D[i, j]
                if dij <= tol:
                    # They're at the same point numerically; pick a random push direction
                    dir_vec = rng.normal(size=2)
                    norm = np.linalg.norm(dir_vec)
                    if norm < 1e-12:
                        dir_vec = np.array([1.0, 0.0])
                        norm = 1.0
                    dir_unit = dir_vec / norm
                    # Push apart equally to achieve distance 1
                    delta = 0.5
                    X[i] -= delta * dir_unit
                    X[j] += delta * dir_unit
                    continue
                dir_unit = (X[j] - X[i]) / dij
                # Need to increase separation to 1: add (1 - dij)/2 along the line
                delta = 0.5 * (1.0 - dij)
                X[i] -= delta * dir_unit
                X[j] += delta * dir_unit
        # Finally, scale to ensure min distance exactly 1 (this can only help diameter)
        X = rescale_to_min_one(X)
        # Center to improve numerical stability
        recenter(X)
        return X

    def diameter_pair(X):
        # Return indices (i,j) with maximum pairwise distance and the distance
        D = pairwise_distances(X)
        m = D.shape[0]
        mask = ~np.eye(m, dtype=bool)
        # Flatten masked distances to find argmax efficiently
        D_flat = D[mask]
        idx = np.argmax(D_flat)
        # Map back to indices
        # Build indices mapping for mask
        # Alternatively, compute full argmax then skip diagonal
        # Simpler: find (i,j) by unravel
        ij = np.argwhere(D == np.max(D_flat))
        # pick first i<j
        for a, b in ij:
            if a < b:
                return a, b, D[a, b]
        a, b = ij[0]
        return a, b, D[a, b]

    # ------------------------------
    # Triangular lattice (hex grid) utilities
    # ------------------------------
    sqrt3 = sqrt(3.0)

    def axial_to_xy(q, r):
        # Triangular lattice basis: e1=(1,0), e2=(1/2, sqrt(3)/2)
        return np.array([q + 0.5 * r, 0.5 * sqrt3 * r])

    def hex_ring_coords(k):
        # Return axial (q,r) for hex ring with axial norm k: max(|q|,|r|,|q+r|)=k
        # Standard generation: walk around ring
        if k == 0:
            return [(0, 0)]
        results = []
        # Directions in axial coordinates (q,r)
        dirs = [(1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1)]
        # Start at (k, 0)
        q, r = k, 0
        for d in range(6):
            dq, dr = dirs[d]
            for _ in range(k):
                results.append((q, r))
                q += -dr  # rotate 60 deg by moving along previous direction? We'll use a standard walk
                r += dq
            # The above step was incorrect; use a robust ring traversal below
        # Implement proper ring traversal:
        results = []
        q, r = (k, 0)
        steps = [(0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1), (1, 0)]
        for dq, dr in steps:
            for _ in range(k):
                results.append((q, r))
                q += dq
                r += dr
        return results

    def triangular_patch_best_169():
        # Construct the 1 + 6 + 9 pattern by choosing 9 out of the 12 ring-2 nodes
        # that minimize the diameter.
        center = [(0, 0)]
        ring1 = hex_ring_coords(1)  # 6 nodes
        ring2 = hex_ring_coords(2)  # 12 nodes

        # Convert axial coords to xy
        c_xy = [axial_to_xy(q, r) for (q, r) in center]
        r1_xy = [axial_to_xy(q, r) for (q, r) in ring1]
        r2_xy = [axial_to_xy(q, r) for (q, r) in ring2]

        # Enumerate all combinations of 9 from 12 ring-2 nodes
        best_pts = None
        best_dmax = np.inf
        # Precompute arrays for performance
        r2_indices = list(range(len(r2_xy)))
        for subset in itertools.combinations(r2_indices, 9):
            pts = np.vstack([c_xy, r1_xy, [r2_xy[i] for i in subset]])
            # Min distance in triangular lattice is >=1, and with ring1 this is exactly 1
            # We can still rescale to ensure min=1 (no-op usually).
            pts = rescale_to_min_one(pts)
            D = pairwise_distances(pts)
            dmax = max_distance(D)
            if dmax < best_dmax - 1e-12:
                best_dmax = dmax
                best_pts = pts.copy()
        return best_pts

    # ------------------------------
    # Feasible contraction
    # ------------------------------
    def feasible_contraction(X, steps=300, step0=0.04, step1=0.004, project_passes=4):
        # Contract the diameter while maintaining min-distance >= 1 via projection.
        X = X.copy()
        X = recenter(X)
        X = project_feasible(X, max_passes=project_passes)
        best = X.copy()
        best_d = max_distance(pairwise_distances(X))

        for t in range(steps):
            alpha = step1 + (step0 - step1) * max(0.0, (steps - t) / steps)
            i, j, dij = diameter_pair(X)
            if dij <= 0:
                break
            # Move i and j toward each other by alpha fraction along the connecting line
            u = (X[j] - X[i]) / dij
            X[i] += alpha * u
            X[j] -= alpha * u

            # Small inward pull for the most outer points (radial contraction)
            # Select top-k largest radii points and pull slightly towards origin
            r = np.linalg.norm(X, axis=1)
            k = min(4, X.shape[0])
            idx_sorted = np.argsort(-r)
            for idx in idx_sorted[:k]:
                if r[idx] > 1e-12:
                    X[idx] -= (alpha * 0.2) * (X[idx] / r[idx])

            # Project to enforce min distance >= 1, then rescale so that min == 1
            X = project_feasible(X, max_passes=project_passes)

            D = pairwise_distances(X)
            dmax = max_distance(D)
            if dmax < best_d - 1e-9:
                best_d = dmax
                best = X.copy()

        return best

    # ------------------------------
    # Additional seeds
    # ------------------------------
    def ring_seed_169():
        # Construct a 1-6-9 ring pattern:
        # - inner 6 points on unit-radius regular hexagon (chords are 1)
        # - outer 9 points on radius r2 with offset angle to maximize spacing
        # Start with r2 slightly conservative, then project to feasibility
        c = np.zeros((1, 2))
        # Inner ring radius r1=1 ensures neighboring distance exactly 1
        r1 = 1.0
        inner = []
        for k in range(6):
            theta = 2 * pi * k / 6.0
            inner.append([r1 * np.cos(theta), r1 * np.sin(theta)])
        inner = np.array(inner)

        # Outer ring: 9 points
        # Minimal r2 to have outer ring chord >= 1: r2 >= 1/(2 sin(pi/9)) ~ 1.461
        r2 = 1.52  # slightly larger to ease feasibility against inner ring
        outer = []
        offset = pi / 6.0 * 0.35  # stagger vs inner ring
        for k in range(9):
            theta = offset + 2 * pi * k / 9.0
            outer.append([r2 * np.cos(theta), r2 * np.sin(theta)])
        outer = np.array(outer)

        pts = np.vstack([c, inner, outer])
        pts = project_feasible(pts, max_passes=6)
        return pts

    def poisson_seed(m=16, target_radius=2.2, tries=2000):
        # Simple Poisson-disk-like seed inside a circle by random rejection and projection
        # Start random points in a disc and then push apart using projection.
        pts = []
        for _ in range(m):
            r = target_radius * sqrt(rng.uniform(0.0, 1.0))
            theta = rng.uniform(0.0, 2 * pi)
            pts.append([r * np.cos(theta), r * np.sin(theta)])
        X = np.array(pts)
        # Apply a few passes of projection to enforce min>=1
        X = project_feasible(X, max_passes=10)
        return X

    # ------------------------------
    # Build seeds and optimize each
    # ------------------------------
    seeds = []

    # 1) Best hex/triangular patch seed (1+6+9 from ring-2)
    tri_seed = triangular_patch_best_169()
    seeds.append(tri_seed)

    # 2) Additional seeds from ring parameterization and Poisson
    seeds.append(ring_seed_169())
    seeds.append(poisson_seed())

    # 3) Perturbations of the hex/triangular seed to create diversity
    for _ in range(3):
        noise = 0.02 * rng.normal(size=tri_seed.shape)
        seeds.append(project_feasible(tri_seed + noise, max_passes=4))

    # Run feasible contraction on each seed and keep the best result
    best_points = None
    best_ratio_sq = np.inf

    for seed in seeds:
        X = feasible_contraction(seed, steps=350, step0=0.05, step1=0.005, project_passes=5)
        D = pairwise_distances(X)
        dmin = min_nonzero_distance(D)
        dmax = max_distance(D)
        ratio_sq = float((dmax / dmin) ** 2)
        if ratio_sq < best_ratio_sq - 1e-12:
            best_ratio_sq = ratio_sq
            best_points = X.copy()

    # Final polish: a short contraction on the current best with smaller steps
    best_points = feasible_contraction(best_points, steps=150, step0=0.02, step1=0.002, project_passes=4)
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

```python
#!/usr/bin/env python3
"""16-point planar construction minimizing (D_max / D_min)^2 via multi-island
smooth min–max descent, projection-repair, and calipers-driven active diameter shrink.

This implements a hybrid of HIMSP (smooth surrogate + projection + shrink)
with improvements:
- Convex-hull rotating calipers to identify active farthest pairs
- Line-searched shrink acting on all active diameter pairs simultaneously
- Mild inward pull on outermost hull points to circularize the boundary
- A set of ring-parameter islands (low-dimensional search on radii and phase offsets)
  that co-exist with free-coordinate islands for diversity and strong seeding.

The solver normalizes D_min=1 throughout, so the objective reduces to D_max^2.
"""

import json
from typing import Tuple, List, Dict, Any

import numpy as np


# EVOLVE_START
def optimize_construct(n=16, d=2):
    """
    Optimize positions of n=16 points in 2D to minimize R^2 = (D_max / D_min)^2.
    We normalize at each step so D_min = 1, therefore objective reduces to D_max^2.

    Returns:
        points: (16,2) numpy array of point coordinates
        ratio_squared: float, (D_max / D_min)^2 for returned configuration
    """
    if (n, d) != (16, 2):
        raise ValueError("the benchmark evaluates n=16 and d=2")

    rng = np.random.default_rng()  # stochastic search; no fixed seed to encourage exploration

    # ------------- Utilities -------------
    def pairwise(points: np.ndarray):
        """Compute pairwise vectors and distances for all pairs i<j."""
        diffs = points[:, None, :] - points[None, :, :]
        dists = np.linalg.norm(diffs, axis=-1)
        np.fill_diagonal(dists, np.inf)
        triu_i, triu_j = np.triu_indices(points.shape[0], k=1)
        vecs = points[triu_i] - points[triu_j]
        ds = dists[triu_i, triu_j]
        return triu_i, triu_j, vecs, ds, dists

    def compute_min_max(points: np.ndarray):
        """Compute D_min (min nonzero pair distance) and D_max (max pair distance)."""
        _, _, _, ds, _ = pairwise(points)
        dmin = float(np.min(ds))
        dmax = float(np.max(ds))
        return dmin, dmax

    def normalize(points: np.ndarray):
        """Center at origin and rescale so minimum pairwise distance equals 1."""
        pts = points.copy()
        pts -= np.mean(pts, axis=0, keepdims=True)
        _, _, _, ds, _ = pairwise(pts)
        dmin = float(np.min(ds))
        if not np.isfinite(dmin) or dmin <= 0:
            # degenerate: add small jitter
            pts += 1e-3 * rng.standard_normal(pts.shape)
            _, _, _, ds, _ = pairwise(pts)
            dmin = float(np.min(ds))
        scale = 1.0 / dmin
        pts *= scale
        return pts

    def rotation_matrix(theta: float):
        c, s = np.cos(theta), np.sin(theta)
        return np.array([[c, -s], [s, c]])

    def hex_lattice_seed() -> np.ndarray:
        """Take points from a triangular lattice near origin; pick 16 nearest."""
        a = np.array([1.0, 0.0])
        b = np.array([0.5, np.sqrt(3) / 2.0])
        coords = []
        R = 4
        for i in range(-R, R + 1):
            for j in range(-R, R + 1):
                p = i * a + j * b
                coords.append(p)
        pts = np.array(coords)
        r = np.linalg.norm(pts, axis=1)
        idx = np.argsort(r)[:16]
        sel = pts[idx]
        sel = sel @ rotation_matrix(rng.uniform(0, 2 * np.pi)).T
        sel += 0.02 * rng.standard_normal(sel.shape)
        return normalize(sel)

    def sunflower_seed() -> np.ndarray:
        """Sunflower (Fermat spiral) seed, compact, then normalized."""
        m = 16
        angles = 2.39996322972865332  # golden angle
        k = np.arange(m)
        r = np.sqrt((k + 0.5) / m)
        theta = k * angles
        pts = np.stack([r * np.cos(theta), r * np.sin(theta)], axis=1)
        pts *= 2.0
        pts = pts @ rotation_matrix(rng.uniform(0, 2 * np.pi)).T
        pts += 0.03 * rng.standard_normal(pts.shape)
        return normalize(pts)

    def two_ring_seed() -> np.ndarray:
        """Two concentric rings: 6 inner, 10 outer, phase-shifted."""
        m1, m2 = 6, 10
        r1 = 1.0 / (2 * np.sin(np.pi / m1))
        r2 = 1.0 / (2 * np.sin(np.pi / m2))
        t1 = np.linspace(0, 2 * np.pi, m1, endpoint=False) + rng.uniform(0, 2 * np.pi)
        t2 = np.linspace(0, 2 * np.pi, m2, endpoint=False) + rng.uniform(0, 2 * np.pi)
        inner = np.stack([r1 * np.cos(t1), r1 * np.sin(t1)], axis=1)
        outer = np.stack([r2 * np.cos(t2), r2 * np.sin(t2)], axis=1)
        pts = np.vstack([inner, outer])
        pts = pts @ rotation_matrix(rng.uniform(0, 2 * np.pi)).T
        pts += 0.02 * rng.standard_normal(pts.shape)
        return normalize(pts)

    def grid_seed() -> np.ndarray:
        """Jittered 4x4 grid."""
        xs = np.arange(4, dtype=float)
        ys = np.arange(4, dtype=float)
        grid = np.array([[x, y] for y in ys for x in xs], dtype=float)
        grid += 0.05 * rng.standard_normal(grid.shape)
        return normalize(grid)

    def random_seed() -> np.ndarray:
        pts = 0.5 * rng.standard_normal((16, 2))
        return normalize(pts)

    def soft_diameter_and_grad(points: np.ndarray, beta: float, lam: float):
        """
        Compute soft diameter surrogate and gradient plus penalty for short pairs.
        E = D_soft + lam * P, where
        D_soft = (1/beta) log sum_{i<j} exp(beta * d_ij)
        P = sum_{i<j} max(0, 1 - d_ij)^2
        Returns:
            E, grad (wrt points) for E, D_soft, P, grad_raw (D_soft + penalty gradient)
        """
        ii, jj, vecs, ds, _ = pairwise(points)
        ds_safe = np.clip(ds, 1e-9, None)

        mx = np.max(beta * ds)
        exps = np.exp(beta * ds - mx)
        Z = np.sum(exps)
        weights = exps / (Z + 1e-18)

        D_soft = (np.log(Z) + mx) / beta

        # Gradient of D_soft
        g = np.zeros_like(points)
        contrib = (weights / ds_safe)[:, None] * vecs
        np.add.at(g, ii, contrib)
        np.add.at(g, jj, -contrib)

        # Penalty for violations
        g_pen = np.zeros_like(points)
        viol = ds < 1.0
        if np.any(viol):
            t = (1.0 - ds[viol])
            factor = (-2.0 * t / np.clip(ds[viol], 1e-9, None))[:, None]
            pen_contrib = factor * vecs[viol]
            np.add.at(g_pen, ii[viol], pen_contrib)
            np.add.at(g_pen, jj[viol], -pen_contrib)
            P = float(np.sum(t**2))
        else:
            P = 0.0

        E = D_soft + lam * P
        g_full = g + lam * g_pen
        return E, g_full, D_soft, P, g_full

    def penalty_grad(points: np.ndarray):
        """Only penalty gradient (hinge below 1)."""
        ii, jj, vecs, ds, _ = pairwise(points)
        g = np.zeros_like(points)
        viol = ds < 1.0
        if not np.any(viol):
            return g, 0.0
        t = (1.0 - ds[viol])
        factor = (-2.0 * t / np.clip(ds[viol], 1e-9, None))[:, None]
        pen_contrib = factor * vecs[viol]
        np.add.at(g, ii[viol], pen_contrib)
        np.add.at(g, jj[viol], -pen_contrib)
        P = float(np.sum(t**2))
        return g, P

    def projection_repair(points: np.ndarray, passes: int = 4, damping: float = 0.9):
        """
        Project all violating pairs to be at least distance 1 by moving points along their connecting lines.
        Perform several random-order passes.
        """
        pts = points.copy()
        for _ in range(passes):
            ii, jj, vecs, ds, _ = pairwise(pts)
            viol = np.where(ds < 1.0)[0]
            if viol.size == 0:
                break
            rng.shuffle(viol)
            for k in viol:
                i = ii[k]
                j = jj[k]
                v = pts[i] - pts[j]
                d = np.linalg.norm(v)
                if d < 1e-12:
                    delta = 0.5 * rng.standard_normal(2)
                    pts[i] += delta
                    pts[j] -= delta
                    continue
                if d >= 1.0:
                    continue
                u = v / d
                gap = (1.0 - d) * 0.5
                shift = damping * gap
                pts[i] += shift * u
                pts[j] -= shift * u
        return pts

    # --- Convex hull (monotone chain) and rotating calipers for diameter pairs ---

    def _cross(o: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
        return float((a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]))

    def convex_hull_indices(points: np.ndarray) -> np.ndarray:
        """
        Andrew's monotone chain convex hull. Returns indices of hull vertices in CCW order.
        Collinear points on edges are kept at endpoints only (strict turn).
        """
        npts = points.shape[0]
        if npts <= 1:
            return np.arange(npts)
        # sort by x, then y
        idx = np.lexsort((points[:, 1], points[:, 0]))
        pts_sorted = points[idx]

        lower = []
        for i in range(npts):
            while len(lower) >= 2 and _cross(pts_sorted[lower[-2]], pts_sorted[lower[-1]], pts_sorted[i]) <= 0:
                lower.pop()
            lower.append(i)
        upper = []
        for i in range(npts - 1, -1, -1):
            while len(upper) >= 2 and _cross(pts_sorted[upper[-2]], pts_sorted[upper[-1]], pts_sorted[i]) <= 0:
                upper.pop()
            upper.append(i)
        # Concatenate, removing last of each (repeats)
        hull_idx_sorted = lower[:-1] + upper[:-1]
        hull_indices = idx[np.array(hull_idx_sorted, dtype=int)]
        # Unique cycle (already CCW)
        return hull_indices

    def rotating_calipers_diameter_pairs(points: np.ndarray) -> List[Tuple[int, int]]:
        """
        Return list of index pairs (i, j) on convex hull that realize the diameter (farthest pair).
        Uses rotating calipers on the convex polygon.
        """
        idx_h = convex_hull_indices(points)
        h = len(idx_h)
        if h == 0:
            return []
        if h == 1:
            return []
        if h == 2:
            return [(idx_h[0], idx_h[1])]
        # polygon in CCW order
        P = points[idx_h]
        # Start with antipodal pair
        j = 1
        max_d2 = -1.0
        pairs = []
        # Function for next index modulo h
        def nxt(x): return (x + 1) % h

        for i in range(h):
            ni = nxt(i)
            # Advance j while area increases
            changed = True
            while changed:
                changed = False
                nj = nxt(j)
                v_i = P[ni] - P[i]
                # Compare area increase: cross(v_i, P[nj]-P[j])
                if abs(_cross(P[i], P[ni], P[nj])) > abs(_cross(P[i], P[ni], P[j])):
                    j = nj
                    changed = True
            # i and j are antipodal. Record distance
            d2 = float(np.sum((P[i] - P[j]) ** 2))
            if d2 > max_d2 + 1e-12:
                max_d2 = d2
                pairs = [(idx_h[i], idx_h[j])]
            elif abs(d2 - max_d2) <= 1e-12:
                pairs.append((idx_h[i], idx_h[j]))
        # Deduplicate pairs (i<j)
        uniq = set()
        out = []
        for (a, b) in pairs:
            if a > b:
                a, b = b, a
            if (a, b) not in uniq:
                uniq.add((a, b))
                out.append((a, b))
        return out

    def active_diameter_shrink_line_search(points: np.ndarray, eps: float, pull_k: int = 4, max_backtracks: int = 6):
        """
        Use rotating calipers to find active diameter pairs. Build a displacement that moves each
        active pair's endpoints toward their midpoint (reducing the diameter). Also apply a mild
        inward radial pull on the outermost 'pull_k' points to round the hull.

        Perform backtracking line search to ensure exact D_max does not increase (after projection & renorm).
        """
        pts0 = points.copy()
        dmin0, dmax0 = compute_min_max(pts0)

        pairs = rotating_calipers_diameter_pairs(pts0)
        if len(pairs) == 0:
            return pts0  # nothing to do

        disp = np.zeros_like(pts0)
        # Sum displacements from all active farthest pairs
        for (i, j) in pairs:
            v = pts0[i] - pts0[j]
            d = np.linalg.norm(v)
            if d < 1e-12:
                continue
            u = v / d
            shift = 0.5 * eps * d
            disp[i] -= shift * u
            disp[j] += shift * u

        # Mild inward pull on the outermost points
        radii = np.linalg.norm(pts0, axis=1)
        if pull_k > 0:
            top_idx = np.argsort(radii)[-pull_k:]
            pull_alpha = 0.25 * eps  # scaled with eps
            for i in top_idx:
                p = pts0[i]
                r = np.linalg.norm(p)
                if r > 1e-9:
                    disp[i] += -pull_alpha * p  # pull toward origin

        # Line search
        s = 1.0
        best_pts = pts0
        best_dmax = dmax0
        accepted = False
        for _ in range(max_backtracks):
            trial = pts0 + s * disp
            trial = projection_repair(trial, passes=2, damping=0.95)
            trial = normalize(trial)
            _, dmax_trial = compute_min_max(trial)
            if dmax_trial <= dmax0 + 1e-10:
                best_pts = trial
                best_dmax = dmax_trial
                accepted = True
                break
            s *= 0.5
        if not accepted:
            return pts0
        return best_pts

    # ------------- Ring-parameter island machinery -------------
    # Ring patterns that sum to 16
    ring_patterns = [
        [1, 6, 9],
        [1, 5, 10],
        [2, 6, 8],
        [4, 6, 6],
    ]

    def ring_min_radius(mk: int) -> float:
        if mk <= 1:
            return 0.0
        return 1.0 / (2.0 * np.sin(np.pi / mk))

    def softplus(x: np.ndarray) -> np.ndarray:
        # numerically stable softplus
        return np.where(x > 20, x, np.log1p(np.exp(x)))

    def ring_params_init(pattern: List[int]) -> Dict[str, Any]:
        K = len(pattern)
        # radii parameters per ring k; for mk==1, radius fixed at 0 (no param)
        log_extra = []
        for mk in pattern:
            if mk <= 1:
                log_extra.append(0.0)  # unused
            else:
                # start near minimal polygon radius
                log_extra.append(rng.normal(loc=-1.0, scale=0.5))
        log_extra = np.array(log_extra, dtype=float)
        # phase offsets per ring: phi[0] = 0 anchored; others random
        phi = np.zeros(K, dtype=float)
        for k in range(1, K):
            phi[k] = rng.uniform(0, 2 * np.pi)
        # step sizes for search
        sigma_r = 0.25
        sigma_phi = 0.25
        return {"pattern": pattern, "log_extra": log_extra, "phi": phi, "sigma_r": sigma_r, "sigma_phi": sigma_phi}

    def ring_params_to_points(pstate: Dict[str, Any]) -> np.ndarray:
        pattern = pstate["pattern"]
        log_extra = pstate["log_extra"]
        phi = pstate["phi"]
        coords = []
        for k, mk in enumerate(pattern):
            if mk <= 1:
                if mk == 1:
                    coords.append(np.array([[0.0, 0.0]]))
                continue
            r = ring_min_radius(mk) + softplus(log_extra[k])
            angles = np.linspace(0, 2 * np.pi, mk, endpoint=False) + phi[k]
            ring = np.stack([r * np.cos(angles), r * np.sin(angles)], axis=1)
            coords.append(ring)
        if len(coords) == 0:
            pts = np.zeros((0, 2))
        else:
            pts = np.vstack(coords)
        # Ensure count is 16 (pattern sums should be 16)
        assert pts.shape[0] == 16, f"pattern {pattern} generated {pts.shape[0]} points"
        return pts

    def ring_param_energy(points: np.ndarray, beta: float = 80.0, lam_pen: float = 200.0) -> float:
        # Penalized soft diameter objective for ring search
        ii, jj, vecs, ds, _ = pairwise(points)
        # D_soft
        mx = np.max(beta * ds)
        exps = np.exp(beta * ds - mx)
        Z = np.sum(exps)
        D_soft = (np.log(Z) + mx) / beta
        # Penalty for violations
        t = np.maximum(0.0, 1.0 - ds)
        P = float(np.sum(t**2))
        return float(D_soft + lam_pen * P)

    def ring_island_step(pstate: Dict[str, Any]) -> Dict[str, Any]:
        """
        Single evolution step on ring parameters via simple (1+lambda) random search.
        """
        pattern = pstate["pattern"]
        log_extra = pstate["log_extra"].copy()
        phi = pstate["phi"].copy()
        sigma_r = pstate["sigma_r"]
        sigma_phi = pstate["sigma_phi"]

        base_pts = ring_params_to_points(pstate)
        base_E = ring_param_energy(base_pts)
        best_log_extra = log_extra.copy()
        best_phi = phi.copy()
        best_E = base_E

        # Try a handful of proposals
        lamb = 6
        for _ in range(lamb):
            cand_log_extra = log_extra.copy()
            cand_phi = phi.copy()
            # mutate radii params (only where mk>1)
            for k, mk in enumerate(pattern):
                if mk > 1:
                    cand_log_extra[k] += sigma_r * rng.standard_normal()
            # mutate phases (except first ring to anchor global rotation)
            for k in range(1, len(pattern)):
                cand_phi[k] = (cand_phi[k] + sigma_phi * rng.standard_normal()) % (2 * np.pi)

            cand_state = {"pattern": pattern, "log_extra": cand_log_extra, "phi": cand_phi,
                          "sigma_r": sigma_r, "sigma_phi": sigma_phi}
            cand_pts = ring_params_to_points(cand_state)
            E = ring_param_energy(cand_pts)
            if E < best_E:
                best_E = E
                best_log_extra = cand_log_extra
                best_phi = cand_phi

        # Adapt step sizes slightly
        improved = best_E < base_E - 1e-10
        if improved:
            sigma_r = min(0.6, sigma_r * 1.05)
            sigma_phi = min(0.6, sigma_phi * 1.05)
            log_extra = best_log_extra
            phi = best_phi
        else:
            sigma_r = max(0.05, sigma_r * 0.98)
            sigma_phi = max(0.05, sigma_phi * 0.98)

        # Update and return
        pstate["log_extra"] = log_extra
        pstate["phi"] = phi
        pstate["sigma_r"] = sigma_r
        pstate["sigma_phi"] = sigma_phi
        return pstate

    # ------------- Island initialization -------------
    def initialize_coord_island(pts: np.ndarray) -> Dict[str, Any]:
        return {
            "type": "coord",
            "pts": pts,
            "vel": np.zeros_like(pts),
            "score": None,
            "best_pts": pts.copy(),
            "best_score": None,
        }

    def initialize_ring_island() -> Dict[str, Any]:
        pattern = ring_patterns[rng.integers(0, len(ring_patterns))]
        pstate = ring_params_init(pattern)
        pts = ring_params_to_points(pstate)
        pts = projection_repair(pts, passes=3, damping=0.95)
        pts = normalize(pts)
        return {
            "type": "ring",
            "params": pstate,
            "pts": pts,
            "score": None,
            "best_pts": pts.copy(),
            "best_score": None,
        }

    def initialize_islands(num_islands: int, ring_fraction: float = 0.33) -> List[dict]:
        islands: List[Dict[str, Any]] = []
        # create ring islands
        num_ring = max(1, int(num_islands * ring_fraction))
        for _ in range(num_ring):
            islands.append(initialize_ring_island())
        # coordinate islands with diverse seeds
        generators = [hex_lattice_seed, two_ring_seed, sunflower_seed, grid_seed, random_seed]
        for t in range(num_islands - num_ring):
            gen = generators[t % len(generators)]
            pts = gen()
            islands.append(initialize_coord_island(pts))
        return islands

    def evaluate(points: np.ndarray):
        pts = normalize(points)
        _, dmax = compute_min_max(pts)
        return pts, dmax**2

    # ------------- Main optimization hyperparameters -------------
    num_islands = 14
    cycles = 200  # outer cycles
    grad_steps_per_cycle = 4
    beta_start, beta_end = 20.0, 80.0
    lam = 60.0  # penalty weight for gradient phase (projection also enforces constraints)
    lr = 0.09  # base learning rate
    momentum = 0.8
    shrink_eps_start, shrink_eps_end = 0.02, 0.004
    projection_passes = 3
    selection_period = 20
    survivors = 5  # islands kept at selection

    islands = initialize_islands(num_islands, ring_fraction=0.35)
    global_best_pts = None
    global_best_score = np.inf

    # ------------- Optimization loop -------------
    for c in range(cycles):
        # anneal parameters
        t = c / max(1, cycles - 1)
        beta = beta_start + (beta_end - beta_start) * t
        shrink_eps = shrink_eps_start + (shrink_eps_end - shrink_eps_start) * t
        # mild decay of learning rate
        lr_c = lr * (0.985 ** c)

        for isl in islands:
            if isl["type"] == "coord":
                pts = isl["pts"]
                vel = isl["vel"]

                # Stage A: Smooth soft-max gradient steps
                for _ in range(grad_steps_per_cycle):
                    _, g_full, _, _, _ = soft_diameter_and_grad(pts, beta=beta, lam=lam)
                    # Add a small penalty nudge to discourage near-violations
                    g_pen, _ = penalty_grad(pts)
                    g = g_full + 0.05 * lam * g_pen

                    vel = momentum * vel - lr_c * g
                    pts = pts + vel
                    pts -= np.mean(pts, axis=0, keepdims=True)

                # Projection repair
                pts = projection_repair(pts, passes=projection_passes, damping=0.9)
                pts = normalize(pts)

                # Stage B: Active diameter shrink via calipers + line search
                pts = active_diameter_shrink_line_search(pts, eps=shrink_eps, pull_k=4, max_backtracks=6)
                # Final repair and normalize
                pts = projection_repair(pts, passes=2, damping=0.95)
                pts = normalize(pts)

                isl["pts"] = pts
                isl["vel"] = np.zeros_like(pts)  # reset velocity after projection/renorm for stability

                # Evaluate
                eval_pts, score = evaluate(pts)
                isl["pts"] = eval_pts
                isl["score"] = score
                if isl["best_score"] is None or score < isl["best_score"]:
                    isl["best_score"] = score
                    isl["best_pts"] = eval_pts.copy()

                if score < global_best_score:
                    global_best_score = score
                    global_best_pts = eval_pts.copy()

            else:
                # Ring-parameter island step
                isl["params"] = ring_island_step(isl["params"])
                pts = ring_params_to_points(isl["params"])
                # quick feasible projection + normalization
                pts = projection_repair(pts, passes=3, damping=0.95)
                pts = normalize(pts)
                isl["pts"] = pts
                # Evaluate exact D_max^2
                eval_pts, score = evaluate(pts)
                isl["pts"] = eval_pts
                isl["score"] = score
                if isl["best_score"] is None or score < isl["best_score"]:
                    isl["best_score"] = score
                    isl["best_pts"] = eval_pts.copy()
                if score < global_best_score:
                    global_best_score = score
                    global_best_pts = eval_pts.copy()

        # Occasional tiny hull jitter for coordinate islands to escape flat plateaus
        if (c + 1) % 30 == 0:
            for isl in islands:
                if isl["type"] == "coord":
                    pts = isl["pts"]
                    jitter = 0.003 * rng.standard_normal(pts.shape)
                    new_pts = normalize(pts + jitter)
                    # accept only if not worse
                    _, old_dmax = compute_min_max(pts)
                    _, new_dmax = compute_min_max(new_pts)
                    if new_dmax <= old_dmax + 1e-10:
                        isl["pts"] = new_pts
                        isl["score"] = new_dmax**2
                        if isl["score"] < (isl.get("best_score") or np.inf):
                            isl["best_score"] = isl["score"]
                            isl["best_pts"] = new_pts.copy()
                        if isl["score"] < global_best_score:
                            global_best_score = isl["score"]
                            global_best_pts = new_pts.copy()

        # Selection and diversification
        if (c + 1) % selection_period == 0 and len(islands) >= 2:
            # Rank islands by current score
            # Some islands may not have "score" if just reinitialized; safeguard with inf
            for isl in islands:
                if isl.get("score") is None:
                    _, sc = evaluate(isl["pts"])
                    isl["score"] = sc
            islands.sort(key=lambda s: s["score"])
            top = islands[:survivors]
            worst = islands[survivors:]

            def crossover_mix(a_pts: np.ndarray, b_pts: np.ndarray):
                # Randomly rotate each and mix half points
                Ra = rotation_matrix(rng.uniform(0, 2 * np.pi))
                Rb = rotation_matrix(rng.uniform(0, 2 * np.pi))
                A = (a_pts @ Ra.T).copy()
                B = (b_pts @ Rb.T).copy()
                idx = np.arange(A.shape[0])
                rng.shuffle(idx)
                k = A.shape[0] // 2
                takeA = np.sort(idx[:k])
                maskA = np.zeros(A.shape[0], dtype=bool)
                maskA[takeA] = True
                mix = np.where(maskA[:, None], A, B)
                mix += 0.02 * rng.standard_normal(mix.shape)
                return normalize(mix)

            # Reinitialize worst islands with a mix of strategies
            for wi in range(len(worst)):
                mode = wi % 4
                if mode == 0 and survivors >= 2:
                    # crossover top two (coordinate mix)
                    new_pts = crossover_mix(top[0]["pts"], top[1]["pts"])
                    worst[wi].clear()
                    worst[wi].update(initialize_coord_island(new_pts))
                elif mode == 1:
                    # fresh ring island
                    worst[wi].clear()
                    worst[wi].update(initialize_ring_island())
                elif mode == 2:
                    # new coordinate seed from template
                    new_pts = two_ring_seed() if rng.random() < 0.5 else hex_lattice_seed()
                    worst[wi].clear()
                    worst[wi].update(initialize_coord_island(new_pts))
                else:
                    # sunflower or random
                    new_pts = sunflower_seed() if rng.random() < 0.7 else random_seed()
                    worst[wi].clear()
                    worst[wi].update(initialize_coord_island(new_pts))

            islands = top + worst  # keep list size the same

    # Final local polish on the best found (few extra projection + shrink sweeps)
    if global_best_pts is None:
        global_best_pts = hex_lattice_seed()
    pts = global_best_pts.copy()
    for _ in range(8):
        pts = projection_repair(pts, passes=4, damping=0.96)
        pts = normalize(pts)
        pts = active_diameter_shrink_line_search(pts, eps=0.004, pull_k=4, max_backtracks=6)
        pts = projection_repair(pts, passes=2, damping=0.98)
        pts = normalize(pts)
    # exact evaluation
    dmin, dmax = compute_min_max(pts)
    if abs(dmin - 1.0) > 1e-6:
        pts = pts / dmin
        dmin, dmax = compute_min_max(pts)

    ratio_squared = float((dmax / dmin) ** 2)
    return pts.astype(float), ratio_squared
# EVOLVE_END


if __name__ == "__main__":
    points, _ = optimize_construct(16, 2)
    print(json.dumps({"points": points.tolist()}))
```

```python
#!/usr/bin/env python3
"""Optimizes 16 planar points to minimize (D_max / D_min)^2 using a hybrid
multi-island projection-based optimizer integrating calipers-based diameter
shrink with backtracking acceptance, MEC/hull guidance, and ring-parameter
micro-CMA proposals.

We enforce D_min >= 1 via local pairwise projection and gentle normalization
and then minimize the squared diameter D_max^2, which equals R^2 once D_min == 1.

Main features (as per the algorithm description):
- Geometry-aware seeds:
  * Exact 1+6+9 triangular-lattice ring-2 enumeration (top-K subsets of 12 choose 9).
  * Rotated/offset hex-lattice patches (pick 16 closest nodes to origin).
  * Multi-ring templates (8–8, 6–10, 7–9, 5–11) parameterized near minimal chord radius.
  * Poisson-like random seeds projected to feasibility for topology diversity.
- Feasibility via local projection-repair and light normalization to set D_min ≈ 1.
- Exact evaluation on the hull via farthest pair (rotating calipers equivalent by brute over hull).
- Island A (Smooth-Projected Caliper Descent):
  * Farthest-pair contraction and mild inward pulls.
  * Calipers-based active diameter shrink on all max-distance pairs with backtracked line search
    accepting only non-worsening exact D_max.
- Island B (MEC/Hull-Guided Contraction):
  * Approx MEC center from farthest pair midpoint; stronger inward pull on hull vertices.
  * Strong short-range repulsion for dij < 1.1.
  * Periodic hull edge squeeze.
  * Calipers-based shrink with backtracking.
- Island C (Ring-Param Micro-CMA):
  * Two-ring parameterization with soft variations in scale and phase.
  * Samples several perturbations per step and keeps the best; injects into islands during migration.
- Migration, selection, and diversification:
  * Maintain ~16 islands, periodically replace the worst with elite-perturbed copies, topology
    restarts, or crossovers; schedules for step cooling.
- Final polish: a few dozen calipers shrinks and projections.

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

    def all_active_diameter_pairs(X, tol=1e-9):
        # Returns list of (i,j) pairs on the hull whose distance is within tol of D_max
        hull = convex_hull_indices(X)
        h = len(hull)
        if h <= 1:
            return [], 0.0
        # Compute all hull pair distances
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
        active = [(i, j) for (i, j), dij2 in dists2.items() if dmax2 - dij2 <= max(tol * dmax2, 1e-12)]
        return active, dmax

    def true_ratio_sq(X):
        # Ensure D_min == 1 via a light normalization and compute D_max^2
        Xn = rescale_to_min_one(X.copy())
        _, _, dmax = farthest_pair_indices(Xn)
        return float(dmax * dmax)

    # ------------------------------
    # Calipers-based shrink with backtracking
    # ------------------------------
    def caliper_shrink_backtrack(X, eps=0.02, max_backtrack=12, tol=1e-12):
        # Compute current exact D_max on hull
        X0 = X.copy()
        X0 = project_feasible(X0, max_passes=3)
        _, _, dmax0 = farthest_pair_indices(X0)
        if dmax0 <= tol:
            return X0, False
        step = float(eps)
        accepted = False
        for _ in range(max_backtrack):
            # Aggregate displacements across all active pairs
            pairs, _ = all_active_diameter_pairs(X0, tol=1e-9)
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
            # Gentle inward pull for outermost hull points to assist shrink
            hull = convex_hull_indices(Xcand)
            if len(hull) > 0:
                # Approx MEC center
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
            # Smooth-Projected Caliper Descent:
            # - Farthest pair contraction and mild inward pulls
            # - Calipers shrink with backtracking (accept non-worsening D_max)
            X = self.X.copy()
            alpha = self._alpha_schedule(self.iter, max(1, total_steps), 0.04, 0.006)
            i, j, dij = farthest_pair_indices(X)
            if dij > 1e-12:
                u = (X[j] - X[i]) / dij
                X[i] += alpha * u
                X[j] -= alpha * u
            # Mild inward pulls for a few outer points
            r = np.linalg.norm(X, axis=1)
            k = min(6, X.shape[0])
            idx_sorted = np.argsort(-r)
            for idx in idx_sorted[:k]:
                if r[idx] > 1e-12:
                    X[idx] -= (alpha * 0.2) * (X[idx] / r[idx])
            # Projection repair
            X = project_feasible(X, max_passes=4)
            # Calipers shrink with backtracking
            X_shrunk, accepted = caliper_shrink_backtrack(X, eps=alpha * 0.9, max_backtrack=10)
            if accepted:
                X = X_shrunk
            # Commit and update best
            self.X = X
            self._update_best()
            self.iter += 1

        def _step_B(self, t_global, total_steps):
            # MEC/Hull-guided contraction with repulsions and calipers shrink
            X = self.X.copy()
            lam = self._alpha_schedule(self.iter, max(1, total_steps), 0.6, 0.15)
            step = self._alpha_schedule(self.iter, max(1, total_steps), 0.06, 0.008)
            trust = self._alpha_schedule(self.iter, max(1, total_steps), 0.08, 0.02)
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
            # Calipers shrink with backtracking
            X_shrunk, accepted = caliper_shrink_backtrack(X, eps=step * 0.8, max_backtrack=10)
            if accepted:
                X = X_shrunk
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
        # One caliper shrink to polish
        Y, _ = caliper_shrink_backtrack(Y, eps=0.02, max_backtrack=8)
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
        Y, _ = caliper_shrink_backtrack(Y, eps=0.02, max_backtrack=8)
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
        Y, _ = caliper_shrink_backtrack(Y, eps=0.02, max_backtrack=8)
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
                    Xj, _ = caliper_shrink_backtrack(Xj, eps=0.01, max_backtrack=6)
                    islands[jidx].X = Xj
                    islands[jidx]._update_best()

    # Final elite polish: feasibility pass + multiple calipers shrinks
    final = global_best_X.copy() if global_best_X is not None else seeds[0]
    final = project_feasible(final, max_passes=8)
    for _ in range(60):
        final, _ = caliper_shrink_backtrack(final, eps=0.015, max_backtrack=10)
        final = project_feasible(final, max_passes=4)

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

```python
#!/usr/bin/env python3
"""16-point planar construction minimizing (D_max / D_min)^2 via multi-island
smooth min–max descent, projection-repair, and calipers-driven active diameter shrink.

This implements a hybrid portfolio solver that combines:
- Smooth surrogate (softmax diameter) descent with projection-based feasibility repair (POCS)
- Convex-hull rotating-calipers to identify all active farthest pairs
- Line-searched shrink acting on all active diameter pairs simultaneously
- MEC-like inward pull on outer hull vertices toward an estimated center from farthest-pair midpoints
- Ring-parameter islands (low-dimensional optimization of radii/phases) for strong seeding and exploration
- Elite archive, crossovers, restarts, and migrations between island families for diversity

We normalize D_min=1 for evaluation/selection so the objective reduces to D_max^2.
"""

import json
from typing import Tuple, List, Dict, Any

import numpy as np


# EVOLVE_START
def optimize_construct(n=16, d=2):
    """
    Optimize positions of n=16 points in 2D to minimize R^2 = (D_max / D_min)^2.
    We normalize at each evaluation so D_min = 1, therefore objective reduces to D_max^2.

    Returns:
        points: (16,2) numpy array of point coordinates
        ratio_squared: float, (D_max / D_min)^2 for returned configuration
    """
    if (n, d) != (16, 2):
        raise ValueError("the benchmark evaluates n=16 and d=2")

    rng = np.random.default_rng()  # stochastic search; no fixed seed to encourage exploration

    # ------------- Utilities -------------
    def pairwise(points: np.ndarray):
        """Compute pairwise vectors and distances for all pairs i<j."""
        diffs = points[:, None, :] - points[None, :, :]
        dists = np.linalg.norm(diffs, axis=-1)
        np.fill_diagonal(dists, np.inf)
        triu_i, triu_j = np.triu_indices(points.shape[0], k=1)
        vecs = points[triu_i] - points[triu_j]
        ds = dists[triu_i, triu_j]
        return triu_i, triu_j, vecs, ds, dists

    def compute_min_max(points: np.ndarray):
        """Compute D_min (min nonzero pair distance) and D_max (max pair distance)."""
        _, _, _, ds, _ = pairwise(points)
        dmin = float(np.min(ds))
        dmax = float(np.max(ds))
        return dmin, dmax

    def normalize(points: np.ndarray):
        """Center at origin and rescale so minimum pairwise distance equals 1."""
        pts = points.copy()
        pts -= np.mean(pts, axis=0, keepdims=True)
        _, _, _, ds, _ = pairwise(pts)
        dmin = float(np.min(ds))
        if not np.isfinite(dmin) or dmin <= 0:
            # degenerate: add small jitter
            pts += 1e-3 * rng.standard_normal(pts.shape)
            _, _, _, ds, _ = pairwise(pts)
            dmin = float(np.min(ds))
        scale = 1.0 / dmin
        pts *= scale
        return pts

    def rotation_matrix(theta: float):
        c, s = np.cos(theta), np.sin(theta)
        return np.array([[c, -s], [s, c]])

    # ------------- Diverse seeding -------------
    def hex_lattice_seed() -> np.ndarray:
        """Take points from a triangular lattice near origin; pick 16 nearest."""
        a = np.array([1.0, 0.0])
        b = np.array([0.5, np.sqrt(3) / 2.0])
        coords = []
        R = 4
        for i in range(-R, R + 1):
            for j in range(-R, R + 1):
                p = i * a + j * b
                coords.append(p)
        pts = np.array(coords)
        r = np.linalg.norm(pts, axis=1)
        idx = np.argsort(r)[:16]
        sel = pts[idx]
        sel = sel @ rotation_matrix(rng.uniform(0, 2 * np.pi)).T
        sel += 0.02 * rng.standard_normal(sel.shape)
        return normalize(sel)

    def sunflower_seed() -> np.ndarray:
        """Sunflower (Fermat spiral) seed, compact, then normalized."""
        m = 16
        angles = 2.39996322972865332  # golden angle
        k = np.arange(m)
        r = np.sqrt((k + 0.5) / m)
        theta = k * angles
        pts = np.stack([r * np.cos(theta), r * np.sin(theta)], axis=1)
        pts *= 2.0
        pts = pts @ rotation_matrix(rng.uniform(0, 2 * np.pi)).T
        pts += 0.03 * rng.standard_normal(pts.shape)
        return normalize(pts)

    def two_ring_seed(split=(6, 10)) -> np.ndarray:
        """Two concentric rings: default 6 inner, 10 outer, phase-shifted."""
        m1, m2 = split
        assert m1 + m2 == 16
        r1 = 1.0 / (2 * np.sin(np.pi / max(3, m1))) if m1 > 1 else 0.0
        r2 = 1.0 / (2 * np.sin(np.pi / max(3, m2)))
        t1 = np.linspace(0, 2 * np.pi, m1, endpoint=False) + rng.uniform(0, 2 * np.pi) if m1 > 0 else np.zeros(0)
        t2 = np.linspace(0, 2 * np.pi, m2, endpoint=False) + rng.uniform(0, 2 * np.pi)
        inner = np.stack([r1 * np.cos(t1), r1 * np.sin(t1)], axis=1) if m1 > 0 else np.zeros((0, 2))
        outer = np.stack([r2 * np.cos(t2), r2 * np.sin(t2)], axis=1)
        pts = np.vstack([inner, outer])
        pts = pts @ rotation_matrix(rng.uniform(0, 2 * np.pi)).T
        pts += 0.02 * rng.standard_normal(pts.shape)
        return normalize(pts)

    def grid_seed() -> np.ndarray:
        """Jittered 4x4 grid."""
        xs = np.arange(4, dtype=float)
        ys = np.arange(4, dtype=float)
        grid = np.array([[x, y] for y in ys for x in xs], dtype=float)
        grid += 0.05 * rng.standard_normal(grid.shape)
        return normalize(grid)

    def random_seed() -> np.ndarray:
        pts = 0.5 * rng.standard_normal((16, 2))
        return normalize(pts)

    def tri_ring2_subset_seeds(sample_k: int = 12) -> List[np.ndarray]:
        """
        Construct seeds from triangular lattice rings:
        1 center, 6 first-ring at radius 1, and choose 9 of 12 from second ring at radius 2.
        Sample 'sample_k' random 9-subsets and return normalized seeds; pick the best few by D_max^2.
        """
        # Triangular lattice basis
        a = np.array([1.0, 0.0])
        b = np.array([0.5, np.sqrt(3) / 2.0])

        # Build rings around origin (0,0)
        center = np.array([[0.0, 0.0]])
        # First ring: 6 neighbors
        first_ring = np.array([a, -a, b, -b, a - b, b - a])

        # Second ring: integer combinations with |i|+|j|+|i+j| = 4 -> distance squared 4?
        # Explicit 12 positions at "hex radius" 2 steps
        second_candidates = []
        steps = 2
        dirs = [a, b, b - a, -a, -b, a - b]
        # Generate ring positions by walking around hexagon at given steps
        cur = steps * a
        second_candidates.append(cur.copy())
        for direction in [b - a, -b, -a, a - b, b, a]:
            for _ in range(steps):
                cur = cur + direction
                second_candidates.append(cur.copy())
        second_ring = np.array(second_candidates[:12])

        seeds = []
        # Sample random 9-subsets of second ring
        choices = []
        for _ in range(max(1, sample_k)):
            idx = np.arange(12)
            rng.shuffle(idx)
            sel = np.sort(idx[:9])
            # avoid duplicates
            key = tuple(sel.tolist())
            if key in choices:
                continue
            choices.append(key)
            pts = np.vstack([center, first_ring, second_ring[sel]])
            pts = pts @ rotation_matrix(rng.uniform(0, 2 * np.pi)).T
            pts += 0.01 * rng.standard_normal(pts.shape)
            pts = normalize(pts)
            seeds.append(pts)

        # Score and pick top few compact ones
        scored = []
        for s in seeds:
            _, dmax = compute_min_max(s)
            scored.append((dmax**2, s))
        scored.sort(key=lambda t: t[0])
        top = [s for (_, s) in scored[:max(4, len(scored) // 2)]]
        return top

    # ------------- Smooth surrogate and penalties -------------
    def soft_diameter_and_grad(points: np.ndarray, beta: float, lam: float):
        """
        Compute soft diameter surrogate and gradient plus penalty for short pairs.
        E = D_soft + lam * P, where
        D_soft = (1/beta) log sum_{i<j} exp(beta * d_ij)
        P = sum_{i<j} max(0, 1 - d_ij)^2
        Returns:
            E, grad (wrt points), D_soft, P, grad_raw
        """
        ii, jj, vecs, ds, _ = pairwise(points)
        ds_safe = np.clip(ds, 1e-9, None)

        mx = np.max(beta * ds)
        exps = np.exp(beta * ds - mx)
        Z = np.sum(exps)
        weights = exps / (Z + 1e-18)

        D_soft = (np.log(Z) + mx) / beta

        # Gradient of D_soft
        g = np.zeros_like(points)
        contrib = (weights / ds_safe)[:, None] * vecs
        np.add.at(g, ii, contrib)
        np.add.at(g, jj, -contrib)

        # Penalty for violations
        g_pen = np.zeros_like(points)
        viol = ds < 1.0
        if np.any(viol):
            t = (1.0 - ds[viol])
            factor = (-2.0 * t / np.clip(ds[viol], 1e-9, None))[:, None]
            pen_contrib = factor * vecs[viol]
            np.add.at(g_pen, ii[viol], pen_contrib)
            np.add.at(g_pen, jj[viol], -pen_contrib)
            P = float(np.sum(t**2))
        else:
            P = 0.0

        E = D_soft + lam * P
        g_full = g + lam * g_pen
        return E, g_full, D_soft, P, g_full

    def penalty_grad(points: np.ndarray):
        """Only penalty gradient (hinge below 1)."""
        ii, jj, vecs, ds, _ = pairwise(points)
        g = np.zeros_like(points)
        viol = ds < 1.0
        if not np.any(viol):
            return g, 0.0
        t = (1.0 - ds[viol])
        factor = (-2.0 * t / np.clip(ds[viol], 1e-9, None))[:, None]
        pen_contrib = factor * vecs[viol]
        np.add.at(g, ii[viol], pen_contrib)
        np.add.at(g, jj[viol], -pen_contrib)
        P = float(np.sum(t**2))
        return g, P

    def projection_repair(points: np.ndarray, passes: int = 4, damping: float = 0.9):
        """
        Project all violating pairs to be at least distance 1 by moving points along their connecting lines.
        Perform several random-order passes.
        """
        pts = points.copy()
        for _ in range(passes):
            ii, jj, vecs, ds, _ = pairwise(pts)
            viol = np.where(ds < 1.0)[0]
            if viol.size == 0:
                break
            rng.shuffle(viol)
            for k in viol:
                i = ii[k]
                j = jj[k]
                v = pts[i] - pts[j]
                d = np.linalg.norm(v)
                if d < 1e-12:
                    delta = 0.5 * rng.standard_normal(2)
                    pts[i] += delta
                    pts[j] -= delta
                    continue
                if d >= 1.0:
                    continue
                u = v / d
                gap = (1.0 - d) * 0.5
                shift = damping * gap
                pts[i] += shift * u
                pts[j] -= shift * u
        return pts

    # --- Convex hull (monotone chain) and rotating calipers for diameter pairs ---

    def _cross(o: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
        return float((a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]))

    def convex_hull_indices(points: np.ndarray) -> np.ndarray:
        """
        Andrew's monotone chain convex hull. Returns indices of hull vertices in CCW order.
        Collinear points on edges are kept at endpoints only (strict turn).
        """
        npts = points.shape[0]
        if npts <= 1:
            return np.arange(npts)
        # sort by x, then y
        idx = np.lexsort((points[:, 1], points[:, 0]))
        pts_sorted = points[idx]

        lower = []
        for i in range(npts):
            while len(lower) >= 2 and _cross(pts_sorted[lower[-2]], pts_sorted[lower[-1]], pts_sorted[i]) <= 0:
                lower.pop()
            lower.append(i)
        upper = []
        for i in range(npts - 1, -1, -1):
            while len(upper) >= 2 and _cross(pts_sorted[upper[-2]], pts_sorted[upper[-1]], pts_sorted[i]) <= 0:
                upper.pop()
            upper.append(i)
        # Concatenate, removing last of each (repeats)
        hull_idx_sorted = lower[:-1] + upper[:-1]
        hull_indices = idx[np.array(hull_idx_sorted, dtype=int)]
        return hull_indices

    def rotating_calipers_diameter_pairs(points: np.ndarray) -> List[Tuple[int, int]]:
        """
        Return list of index pairs (i, j) on convex hull that realize the diameter (farthest pair).
        Uses rotating calipers on the convex polygon. Ensures all diameter-realizing pairs are returned.
        """
        idx_h = convex_hull_indices(points)
        h = len(idx_h)
        if h <= 1:
            return []
        if h == 2:
            return [(idx_h[0], idx_h[1])]
        # polygon in CCW order
        P = points[idx_h]
        j = 1
        max_d2 = -1.0
        pairs = []
        def nxt(x): return (x + 1) % h

        for i in range(h):
            ni = nxt(i)
            # advance j while area increases
            while True:
                nj = nxt(j)
                # area test using cross product: increase if cross(P[i]->P[ni], P[i]->P[nj]) > cross(..., P[i]->P[j])
                if _cross(P[i], P[ni], P[nj]) > _cross(P[i], P[ni], P[j]):
                    j = nj
                else:
                    break
            # record antipodal pair
            d2 = float(np.sum((P[i] - P[j]) ** 2))
            if d2 > max_d2 + 1e-12:
                max_d2 = d2
                pairs = [(idx_h[i], idx_h[j])]
            elif abs(d2 - max_d2) <= 1e-12:
                pairs.append((idx_h[i], idx_h[j]))
        # Deduplicate pairs (i<j)
        uniq = set()
        out = []
        for (a, b) in pairs:
            if a > b:
                a, b = b, a
            if (a, b) not in uniq:
                uniq.add((a, b))
                out.append((a, b))
        return out

    def estimate_center_from_diameter_pairs(points: np.ndarray, pairs: List[Tuple[int, int]]) -> np.ndarray:
        """Estimate a center as the mean of midpoints of active farthest pairs."""
        if not pairs:
            return np.zeros(2)
        mids = [(points[i] + points[j]) * 0.5 for (i, j) in pairs]
        mids = np.array(mids)
        return np.mean(mids, axis=0)

    def active_diameter_shrink_line_search(points: np.ndarray, eps: float, pull_k: int = 4, max_backtracks: int = 6):
        """
        Use rotating calipers to find active diameter pairs. Build a displacement that moves each
        active pair's endpoints toward their midpoint (reducing the diameter). Also apply a mild
        inward radial pull on the outermost 'pull_k' points toward the estimated center from the
        set of farthest-pair midpoints (approximate MEC center).

        Perform backtracking line search to ensure exact D_max does not increase (after projection & renorm).
        """
        pts0 = points.copy()
        dmin0, dmax0 = compute_min_max(pts0)

        pairs = rotating_calipers_diameter_pairs(pts0)
        if len(pairs) == 0:
            return pts0  # nothing to do

        disp = np.zeros_like(pts0)
        # Sum displacements from all active farthest pairs
        for (i, j) in pairs:
            v = pts0[i] - pts0[j]
            d = np.linalg.norm(v)
            if d < 1e-12:
                continue
            u = v / d
            # shrink endpoints symmetrically toward midpoint
            shift = 0.5 * eps * d
            disp[i] -= shift * u
            disp[j] += shift * u

        # Mild inward pull on the outermost points toward estimated center
        center_est = estimate_center_from_diameter_pairs(pts0, pairs)
        radii = np.linalg.norm(pts0 - center_est, axis=1)
        if pull_k > 0:
            top_idx = np.argsort(radii)[-pull_k:]
            pull_alpha = 0.35 * eps  # slightly stronger than origin pull; scaled with eps
            for i in top_idx:
                p = pts0[i] - center_est
                r = np.linalg.norm(p)
                if r > 1e-9:
                    disp[i] += -pull_alpha * p  # pull toward center_est

        # Line search with feasibility repair and normalization for evaluation
        s = 1.0
        best_pts = pts0
        best_dmax = dmax0
        for _ in range(max_backtracks):
            trial = pts0 + s * disp
            trial = projection_repair(trial, passes=2, damping=0.95)
            trial = normalize(trial)
            _, dmax_trial = compute_min_max(trial)
            if dmax_trial <= dmax0 + 1e-10:
                best_pts = trial
                best_dmax = dmax_trial
                break
            s *= 0.5
        return best_pts

    # ------------- Simple contact-graph polish -------------
    def contact_graph_polish(points: np.ndarray, tiny_eps: float = 0.002, iters: int = 8):
        """
        Perform tiny calipers-based shrink steps interleaved with POCS to gently polish the configuration.
        Acceptance is exact (non-worsening D_max after normalization).
        """
        pts = points.copy()
        _, base_dmax = compute_min_max(pts)
        for _ in range(iters):
            cand = active_diameter_shrink_line_search(pts, eps=tiny_eps, pull_k=3, max_backtracks=4)
            cand = projection_repair(cand, passes=2, damping=0.98)
            cand = normalize(cand)
            _, dmax_cand = compute_min_max(cand)
            if dmax_cand <= base_dmax + 1e-12:
                pts = cand
                base_dmax = dmax_cand
        return pts

    # ------------- Ring-parameter island machinery -------------
    # Ring patterns that sum to 16
    ring_patterns = [
        [8, 8],
        [6, 10],
        [7, 9],
        [5, 11],
        [1, 6, 9],
        [2, 6, 8],
        [1, 5, 10],
        [4, 6, 6],
    ]

    def ring_min_radius(mk: int) -> float:
        if mk <= 1:
            return 0.0
        return 1.0 / (2.0 * np.sin(np.pi / mk))

    def softplus(x: np.ndarray) -> np.ndarray:
        # numerically stable softplus
        return np.where(x > 20, x, np.log1p(np.exp(x)))

    def ring_params_init(pattern: List[int]) -> Dict[str, Any]:
        K = len(pattern)
        # radii parameters per ring k; for mk==1, radius fixed at 0 (no param)
        log_extra = []
        for mk in pattern:
            if mk <= 1:
                log_extra.append(0.0)  # unused
            else:
                # start near minimal polygon radius with slack
                log_extra.append(rng.normal(loc=-0.7, scale=0.6))
        log_extra = np.array(log_extra, dtype=float)
        # phase offsets per ring: phi[0] = 0 anchored; others random
        phi = np.zeros(K, dtype=float)
        for k in range(1, K):
            phi[k] = rng.uniform(0, 2 * np.pi)
        # step sizes for search
        sigma_r = 0.28
        sigma_phi = 0.28
        return {"pattern": pattern, "log_extra": log_extra, "phi": phi, "sigma_r": sigma_r, "sigma_phi": sigma_phi}

    def ring_params_to_points(pstate: Dict[str, Any]) -> np.ndarray:
        pattern = pstate["pattern"]
        log_extra = pstate["log_extra"]
        phi = pstate["phi"]
        coords = []
        for k, mk in enumerate(pattern):
            if mk <= 1:
                if mk == 1:
                    coords.append(np.array([[0.0, 0.0]]))
                continue
            r = ring_min_radius(mk) + softplus(log_extra[k])
            angles = np.linspace(0, 2 * np.pi, mk, endpoint=False) + phi[k]
            ring = np.stack([r * np.cos(angles), r * np.sin(angles)], axis=1)
            coords.append(ring)
        if len(coords) == 0:
            pts = np.zeros((0, 2))
        else:
            pts = np.vstack(coords)
        assert pts.shape[0] == 16, f"pattern {pattern} generated {pts.shape[0]} points"
        return pts

    def ring_param_energy(points: np.ndarray, beta: float = 80.0, lam_pen: float = 200.0) -> float:
        # Penalized soft diameter objective for ring search
        ii, jj, vecs, ds, _ = pairwise(points)
        # D_soft
        mx = np.max(beta * ds)
        exps = np.exp(beta * ds - mx)
        Z = np.sum(exps)
        D_soft = (np.log(Z) + mx) / beta
        # Penalty for violations
        t = np.maximum(0.0, 1.0 - ds)
        P = float(np.sum(t**2))
        return float(D_soft + lam_pen * P)

    def ring_island_step(pstate: Dict[str, Any]) -> Dict[str, Any]:
        """
        Single evolution step on ring parameters via simple (1+lambda) random search.
        """
        pattern = pstate["pattern"]
        log_extra = pstate["log_extra"].copy()
        phi = pstate["phi"].copy()
        sigma_r = pstate["sigma_r"]
        sigma_phi = pstate["sigma_phi"]

        base_pts = ring_params_to_points(pstate)
        base_E = ring_param_energy(base_pts)
        best_log_extra = log_extra.copy()
        best_phi = phi.copy()
        best_E = base_E

        # Try a handful of proposals
        lamb = 8
        for _ in range(lamb):
            cand_log_extra = log_extra.copy()
            cand_phi = phi.copy()
            # mutate radii params (only where mk>1)
            for k, mk in enumerate(pattern):
                if mk > 1:
                    cand_log_extra[k] += sigma_r * rng.standard_normal()
            # mutate phases (except first ring to anchor global rotation)
            for k in range(1, len(pattern)):
                cand_phi[k] = (cand_phi[k] + sigma_phi * rng.standard_normal()) % (2 * np.pi)

            cand_state = {"pattern": pattern, "log_extra": cand_log_extra, "phi": cand_phi,
                          "sigma_r": sigma_r, "sigma_phi": sigma_phi}
            cand_pts = ring_params_to_points(cand_state)
            E = ring_param_energy(cand_pts)
            if E < best_E:
                best_E = E
                best_log_extra = cand_log_extra
                best_phi = cand_phi

        # Adapt step sizes slightly
        improved = best_E < base_E - 1e-12
        if improved:
            sigma_r = min(0.65, sigma_r * 1.06)
            sigma_phi = min(0.65, sigma_phi * 1.06)
            log_extra = best_log_extra
            phi = best_phi
        else:
            sigma_r = max(0.05, sigma_r * 0.985)
            sigma_phi = max(0.05, sigma_phi * 0.985)

        # Update and return
        pstate["log_extra"] = log_extra
        pstate["phi"] = phi
        pstate["sigma_r"] = sigma_r
        pstate["sigma_phi"] = sigma_phi
        return pstate

    # ------------- Island initialization -------------
    def initialize_coord_island(pts: np.ndarray) -> Dict[str, Any]:
        return {
            "type": "coord",
            "pts": pts,
            "vel": np.zeros_like(pts),
            "score": None,
            "best_pts": pts.copy(),
            "best_score": None,
        }

    def initialize_ring_island() -> Dict[str, Any]:
        pattern = ring_patterns[rng.integers(0, len(ring_patterns))]
        pstate = ring_params_init(pattern)
        pts = ring_params_to_points(pstate)
        pts = projection_repair(pts, passes=3, damping=0.95)
        pts = normalize(pts)
        return {
            "type": "ring",
            "params": pstate,
            "pts": pts,
            "score": None,
            "best_pts": pts.copy(),
            "best_score": None,
        }

    def initialize_islands(num_islands: int, ring_fraction: float = 0.33) -> List[dict]:
        islands: List[Dict[str, Any]] = []
        # Precompute a few high-quality triangular-ring seeds
        tri_seeds = tri_ring2_subset_seeds(sample_k=14)

        # create ring islands
        num_ring = max(1, int(num_islands * ring_fraction))
        for _ in range(num_ring):
            islands.append(initialize_ring_island())
        # coordinate islands with diverse seeds
        generators = [hex_lattice_seed, sunflower_seed, grid_seed, random_seed]
        ring_splits = [(6, 10), (7, 9), (8, 8), (5, 11)]
        tri_ptr = 0
        for t in range(num_islands - num_ring):
            mode = t % 6
            if mode == 0 and tri_ptr < len(tri_seeds):
                pts = tri_seeds[tri_ptr]
                tri_ptr += 1
            elif mode == 1:
                pts = two_ring_seed(split=ring_splits[rng.integers(0, len(ring_splits))])
            else:
                gen = generators[rng.integers(0, len(generators))]
                pts = gen()
            islands.append(initialize_coord_island(pts))
        return islands

    def evaluate(points: np.ndarray):
        pts = normalize(points)
        _, dmax = compute_min_max(pts)
        return pts, dmax**2

    # ------------- Elite archive -------------
    def contact_fingerprint(points: np.ndarray, eps: float = 1e-2) -> Tuple[int, ...]:
        """Fingerprint by listing sorted indices of pairs with distance within [1, 1+eps]."""
        ii, jj, _, ds, _ = pairwise(points)
        mask = (ds >= 1.0) & (ds <= 1.0 + eps)
        pairs = list(zip(ii[mask].tolist(), jj[mask].tolist()))
        pairs_sorted = tuple(sorted([min(a, b) * 100 + max(a, b) for a, b in pairs]))
        return pairs_sorted

    class EliteArchive:
        def __init__(self, cap: int = 16):
            self.cap = cap
            self.items: List[Dict[str, Any]] = []

        def add(self, pts: np.ndarray, score: float):
            fp = contact_fingerprint(pts, eps=1.5e-2)
            self.items.append({"score": score, "pts": pts.copy(), "fp": fp})
            # deduplicate by fingerprint, keep best
            best_by_fp: Dict[Tuple[int, ...], Dict[str, Any]] = {}
            for it in self.items:
                f = it["fp"]
                if f not in best_by_fp or it["score"] < best_by_fp[f]["score"]:
                    best_by_fp[f] = it
            self.items = sorted(best_by_fp.values(), key=lambda x: x["score"])[: self.cap]

        def best(self):
            if not self.items:
                return None
            return self.items[0]["pts"].copy(), self.items[0]["score"]

        def random(self):
            if not self.items:
                return None
            k = rng.integers(0, len(self.items))
            it = self.items[k]
            return it["pts"].copy(), it["score"]

    # ------------- Main optimization hyperparameters -------------
    num_islands = 16
    cycles = 260  # outer cycles
    grad_steps_per_cycle = 4
    beta_start, beta_end = 18.0, 90.0
    lam = 80.0  # penalty weight for gradient phase (projection also enforces constraints)
    lr = 0.085  # base learning rate
    momentum = 0.85
    shrink_eps_start, shrink_eps_end = 0.022, 0.0035
    selection_period = 24
    survivors = 6  # islands kept at selection
    elite_cap = 18

    islands = initialize_islands(num_islands, ring_fraction=0.35)
    global_best_pts = None
    global_best_score = np.inf
    archive = EliteArchive(cap=elite_cap)

    # ------------- Optimization loop -------------
    for c in range(cycles):
        # anneal parameters
        t = c / max(1, cycles - 1)
        beta = beta_start + (beta_end - beta_start) * t
        shrink_eps = shrink_eps_start + (shrink_eps_end - shrink_eps_start) * t
        # mild decay of learning rate and gradient noise
        lr_c = lr * (0.985 ** c)
        g_noise = 0.02 * (1.0 - t)

        # adapt projection passes: more early, fewer late
        projection_passes = 4 if t < 0.6 else 3

        for isl in islands:
            if isl["type"] == "coord":
                pts = isl["pts"]
                vel = isl["vel"]

                # Stage A: Smooth soft-max gradient steps with small noise
                for _ in range(grad_steps_per_cycle):
                    _, g_full, _, _, _ = soft_diameter_and_grad(pts, beta=beta, lam=lam)
                    # Add a small penalty nudge to discourage near-violations
                    g_pen, _ = penalty_grad(pts)
                    g = g_full + 0.05 * lam * g_pen
                    if g_noise > 0:
                        g += g_noise * rng.standard_normal(g.shape)

                    vel = momentum * vel - lr_c * g
                    pts = pts + vel
                    pts -= np.mean(pts, axis=0, keepdims=True)

                # Projection repair and normalize only at checkpoint
                pts = projection_repair(pts, passes=projection_passes, damping=0.9)
                pts = normalize(pts)

                # Stage B: Active diameter shrink via calipers + line search
                pts = active_diameter_shrink_line_search(pts, eps=shrink_eps, pull_k=5, max_backtracks=6)
                # Final repair and normalize
                pts = projection_repair(pts, passes=2, damping=0.96)
                pts = normalize(pts)

                # Occasionally interleave a tiny polish step
                if (c % 15) == 7:
                    pts = contact_graph_polish(pts, tiny_eps=0.0015, iters=4)

                isl["pts"] = pts
                isl["vel"] = np.zeros_like(pts)  # reset velocity after projection/renorm for stability

                # Evaluate
                eval_pts, score = evaluate(pts)
                isl["pts"] = eval_pts
                isl["score"] = score
                if isl["best_score"] is None or score < isl["best_score"]:
                    isl["best_score"] = score
                    isl["best_pts"] = eval_pts.copy()

                if score < global_best_score:
                    global_best_score = score
                    global_best_pts = eval_pts.copy()
                    archive.add(global_best_pts, global_best_score)

            else:
                # Ring-parameter island step
                isl["params"] = ring_island_step(isl["params"])
                pts = ring_params_to_points(isl["params"])
                # quick feasible projection + normalization
                pts = projection_repair(pts, passes=3, damping=0.95)
                pts = normalize(pts)
                # tiny calipers shrink for ring candidates as well
                pts = active_diameter_shrink_line_search(pts, eps=0.006, pull_k=3, max_backtracks=3)
                pts = normalize(pts)
                isl["pts"] = pts
                # Evaluate exact D_max^2
                eval_pts, score = evaluate(pts)
                isl["pts"] = eval_pts
                isl["score"] = score
                if isl["best_score"] is None or score < isl["best_score"]:
                    isl["best_score"] = score
                    isl["best_pts"] = eval_pts.copy()
                if score < global_best_score:
                    global_best_score = score
                    global_best_pts = eval_pts.copy()
                    archive.add(global_best_pts, global_best_score)

        # Occasional tiny hull jitter for coordinate islands to escape flat plateaus
        if (c + 1) % 30 == 0:
            for isl in islands:
                if isl["type"] == "coord":
                    pts = isl["pts"]
                    jitter = 0.003 * rng.standard_normal(pts.shape)
                    new_pts = normalize(pts + jitter)
                    # accept only if not worse
                    _, old_dmax = compute_min_max(pts)
                    _, new_dmax = compute_min_max(new_pts)
                    if new_dmax <= old_dmax + 1e-10:
                        isl["pts"] = new_pts
                        isl["score"] = new_dmax**2
                        if isl["score"] < (isl.get("best_score") or np.inf):
                            isl["best_score"] = isl["score"]
                            isl["best_pts"] = new_pts.copy()
                        if isl["score"] < global_best_score:
                            global_best_score = isl["score"]
                            global_best_pts = new_pts.copy()
                            archive.add(global_best_pts, global_best_score)

        # Periodic migration: inject best ring into a coord island and vice versa
        if (c + 1) % 40 == 0 and len(islands) >= 2:
            # Identify best ring and best coord islands
            ring_islands = [isl for isl in islands if isl["type"] == "ring"]
            coord_islands = [isl for isl in islands if isl["type"] == "coord"]
            if ring_islands and coord_islands:
                best_ring = min(ring_islands, key=lambda x: x.get("score", np.inf))
                worst_coord = max(coord_islands, key=lambda x: x.get("score", -np.inf))
                # Inject ring best into a coordinate island (symmetry release)
                new_pts = best_ring["pts"].copy()
                # add minor asymmetric jitter to release symmetry
                new_pts += 0.01 * rng.standard_normal(new_pts.shape)
                new_pts = projection_repair(new_pts, passes=3, damping=0.96)
                new_pts = normalize(new_pts)
                worst_coord.clear()
                worst_coord.update(initialize_coord_island(new_pts))

        # Selection and diversification
        if (c + 1) % selection_period == 0 and len(islands) >= 2:
            # Rank islands by current score
            for isl in islands:
                if isl.get("score") is None:
                    _, sc = evaluate(isl["pts"])
                    isl["score"] = sc
            islands.sort(key=lambda s: s["score"])
            top = islands[:survivors]
            worst = islands[survivors:]

            def crossover_mix(a_pts: np.ndarray, b_pts: np.ndarray):
                # Randomly rotate each and mix half points
                Ra = rotation_matrix(rng.uniform(0, 2 * np.pi))
                Rb = rotation_matrix(rng.uniform(0, 2 * np.pi))
                A = (a_pts @ Ra.T).copy()
                B = (b_pts @ Rb.T).copy()
                idx = np.arange(A.shape[0])
                rng.shuffle(idx)
                k = A.shape[0] // 2
                takeA = np.sort(idx[:k])
                maskA = np.zeros(A.shape[0], dtype=bool)
                maskA[takeA] = True
                mix = np.where(maskA[:, None], A, B)
                mix += 0.02 * rng.standard_normal(mix.shape)
                return normalize(mix)

            # Reinitialize worst islands with a mix of strategies
            for wi in range(len(worst)):
                mode = wi % 5
                if mode == 0 and survivors >= 2:
                    # crossover top two (coordinate mix)
                    new_pts = crossover_mix(top[0]["pts"], top[1]["pts"])
                    worst[wi].clear()
                    worst[wi].update(initialize_coord_island(new_pts))
                elif mode == 1:
                    # fresh ring island
                    worst[wi].clear()
                    worst[wi].update(initialize_ring_island())
                elif mode == 2:
                    # elite perturb-and-polish
                    elite = archive.random()
                    if elite is not None:
                        epts, _ = elite
                        new_pts = epts + 0.01 * rng.standard_normal(epts.shape)
                        new_pts = projection_repair(new_pts, passes=3, damping=0.95)
                        new_pts = active_diameter_shrink_line_search(new_pts, eps=0.006, pull_k=3, max_backtracks=4)
                        new_pts = normalize(new_pts)
                    else:
                        new_pts = two_ring_seed()
                    worst[wi].clear()
                    worst[wi].update(initialize_coord_island(new_pts))
                elif mode == 3:
                    # new coordinate seed from template
                    new_pts = two_ring_seed(split=(6, 10)) if rng.random() < 0.5 else hex_lattice_seed()
                    worst[wi].clear()
                    worst[wi].update(initialize_coord_island(new_pts))
                else:
                    # sunflower or random
                    new_pts = sunflower_seed() if rng.random() < 0.7 else random_seed()
                    worst[wi].clear()
                    worst[wi].update(initialize_coord_island(new_pts))

            islands = top + worst  # keep list size the same

    # Final elite polish
    if global_best_pts is None:
        global_best_pts = hex_lattice_seed()
    pts = global_best_pts.copy()
    for _ in range(10):
        pts = projection_repair(pts, passes=5, damping=0.97)
        pts = normalize(pts)
        pts = active_diameter_shrink_line_search(pts, eps=0.0035, pull_k=5, max_backtracks=6)
        pts = projection_repair(pts, passes=2, damping=0.98)
        pts = contact_graph_polish(pts, tiny_eps=0.0012, iters=6)
        pts = normalize(pts)
    # exact evaluation
    dmin, dmax = compute_min_max(pts)
    if abs(dmin - 1.0) > 1e-6:
        pts = pts / dmin
        dmin, dmax = compute_min_max(pts)

    ratio_squared = float((dmax / dmin) ** 2)
    return pts.astype(float), ratio_squared
# EVOLVE_END


if __name__ == "__main__":
    points, _ = optimize_construct(16, 2)
    print(json.dumps({"points": points.tolist()}))
```

```python
#!/usr/bin/env python3
"""Optimized 16-point planar construction minimizing max/min distance ratio.

This implementation follows a diversified, acceptance-controlled portfolio
of moves combining exact geometric evaluation with parameterized ring-manifold
exploration and feasibility-preserving projections. It targets n=16, d=2 and
returns a 2D NumPy array of points and the ratio squared R^2.

Core elements:
- Exact objective evaluation on the true D_max and D_min with Euclidean distance
- Normalize to D_min = 1, so R^2 = D_max^2
- POCS-like feasibility projection to enforce pairwise distances ≥ 1
- Minimum Enclosing Circle (MEC) to center affine squeezes
- Convex hull and gap-aware tangential equalization moves
- Rank-2 anisotropic squeezes guided by PCA of active near-diameter pairs
- Antipodal pivot micro-moves on hull farthest pairs
- Calipers-inspired active-pair shrink on farthest hull pairs
- Convex-width scan rank-1 squeeze along worst direction
- Contact-graph Laplacian polish on near-contact edges
- Two-ring parameter manifold search (splits like 8–8, 6–10, 7–9, 5–11)
- Multi-armed bandit scheduler to adaptively select productive moves
- Migration and restarts across islands with periodic elite archiving

The algorithm aims to monotonically decrease D_max (with D_min kept at 1) and
is designed to be robust against plateaus around R^2 ≈ 12.9.

Note: For simplicity and robustness in Python, exact rotating calipers are
replaced by brute-force pair distance for D_max; the convex hull is still
computed for move guidance.

"""

import json
import math
import random
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np


# EVOLVE_START

# -------------------------------
# Utility geometry functions
# -------------------------------

def pairwise_dists(points: np.ndarray) -> np.ndarray:
    """Compute pairwise Euclidean distances for points (n,2)."""
    diff = points[:, None, :] - points[None, :, :]
    return np.linalg.norm(diff, axis=-1)


def objective_ratio_squared(points: np.ndarray) -> float:
    """Compute true ratio squared R^2 = (D_max / D_min)^2 for given points."""
    D = pairwise_dists(points)
    # exclude zero distances (same index)
    nonzero = D[D > 1e-12]
    if nonzero.size == 0:
        return float('inf')
    dmin = float(np.min(nonzero))
    dmax = float(np.max(nonzero))
    if dmin <= 0:
        return float('inf')
    return (dmax / dmin) ** 2


def normalize_min_distance(points: np.ndarray, target_min=1.0) -> np.ndarray:
    """Scale points so that the minimum pairwise distance equals target_min."""
    D = pairwise_dists(points)
    nonzero = D[D > 1e-12]
    if nonzero.size == 0:
        return points
    dmin = float(np.min(nonzero))
    if dmin <= 0:
        return points
    scale = target_min / dmin
    return points * scale


def recenter(points: np.ndarray) -> np.ndarray:
    """Subtract centroid to center points."""
    c = np.mean(points, axis=0)
    return points - c


# -------------------------------
# Minimum Enclosing Circle (Welzl)
# -------------------------------

def circle_from_2(a: np.ndarray, b: np.ndarray) -> Tuple[np.ndarray, float]:
    c = (a + b) / 2.0
    r = float(np.linalg.norm(a - c))
    return c, r

def circle_from_3(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> Tuple[np.ndarray, float]:
    # Circumcircle of triangle abc
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2 * (ax*(by - cy) + bx*(cy - ay) + cx*(ay - by))
    if abs(d) < 1e-12:
        # Points collinear; return circle from 2 most distant
        pts = np.array([a, b, c])
        max_pair = (0, 1)
        max_dist = 0.0
        for i in range(3):
            for j in range(i+1, 3):
                dist = np.linalg.norm(pts[i] - pts[j])
                if dist > max_dist:
                    max_dist = dist
                    max_pair = (i, j)
        return circle_from_2(pts[max_pair[0]], pts[max_pair[1]])
    ux = ((ax**2 + ay**2)*(by - cy) + (bx**2 + by**2)*(cy - ay) + (cx**2 + cy**2)*(ay - by)) / d
    uy = ((ax**2 + ay**2)*(cx - bx) + (bx**2 + by**2)*(ax - cx) + (cx**2 + cy**2)*(bx - ax)) / d
    center = np.array([ux, uy], dtype=float)
    radius = float(np.linalg.norm(center - a))
    return center, radius

def mec_welzl(points: np.ndarray) -> Tuple[np.ndarray, float]:
    """Compute Minimum Enclosing Circle using randomized Welzl's algorithm."""
    # Properly shuffled list of points
    P_list = [np.array(p, dtype=float) for p in points]
    random.shuffle(P_list)
    def mec(PL: List[np.ndarray], RL: List[np.ndarray]) -> Tuple[np.ndarray, float]:
        if not PL or len(RL) == 3:
            if len(RL) == 0:
                return np.array([0.0, 0.0]), 0.0
            elif len(RL) == 1:
                return RL[0], 0.0
            elif len(RL) == 2:
                return circle_from_2(RL[0], RL[1])
            else:  # 3 points
                return circle_from_3(RL[0], RL[1], RL[2])
        p = PL.pop()
        c, r = mec(PL, RL)
        if np.linalg.norm(p - c) <= r + 1e-12:
            PL.append(p)
            return c, r
        RL.append(p)
        c, r = mec(PL, RL)
        RL.pop()
        PL.append(p)
        return c, r

    c, r = mec(P_list, [])
    return c, r


# -------------------------------
# Convex Hull (Monotone chain)
# -------------------------------

def convex_hull_indices(points: np.ndarray) -> List[int]:
    """Return indices of convex hull points in CCW order."""
    pts = [(points[i, 0], points[i, 1], i) for i in range(points.shape[0])]
    pts.sort()  # sort by x, then y
    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
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
    return [p[2] for p in hull]


# -------------------------------
# Feasibility projection (POCS)
# -------------------------------

def pocs_enforce_min_distance(points: np.ndarray, target_min=1.0, max_iter=80, tol=1e-5, step_clip=0.25) -> np.ndarray:
    """Push violating pairs apart until all pairwise distances >= target_min.

    Points are moved minimally and symmetrically along the pair direction.
    After convergence, recenter and lightly rescale to set the exact min distance.
    """
    P = points.copy()
    n = P.shape[0]
    for _ in range(max_iter):
        D = pairwise_dists(P)
        violated = np.where(D < target_min - 1e-9)
        if violated[0].size == 0:
            break
        moves = np.zeros_like(P)
        counts = np.zeros(n, dtype=int)
        for i, j in zip(violated[0], violated[1]):
            if i >= j:
                continue
            dij = D[i, j]
            if dij <= 1e-12:
                # Nearly coincident; random small push
                dir_vec = np.random.randn(2)
                dir_norm = np.linalg.norm(dir_vec)
                if dir_norm < 1e-12:
                    dir_vec = np.array([1.0, 0.0])
                else:
                    dir_vec = dir_vec / dir_norm
                delta = 0.5 * (target_min) * dir_vec
            else:
                # push to reach target_min
                dir_vec = (P[i] - P[j]) / (dij + 1e-12)
                delta_mag = 0.5 * (target_min - dij)
                delta_mag = min(delta_mag, step_clip)  # trust-region clip
                delta = delta_mag * dir_vec
            moves[i] += delta
            moves[j] -= delta
            counts[i] += 1
            counts[j] += 1
        # average moves to reduce oscillation
        for idx in range(n):
            if counts[idx] > 0:
                P[idx] += moves[idx] / counts[idx]
        P = recenter(P)
    # final rescale to set min exactly target_min
    P = normalize_min_distance(P, target_min)
    P = recenter(P)
    return P


# -------------------------------
# Active farthest pairs and PCA
# -------------------------------

def active_farthest_pairs(points: np.ndarray, fraction=0.05) -> List[Tuple[int, int]]:
    """Return list of pairs whose distances are within top fraction of D_max."""
    D = pairwise_dists(points)
    dmax = float(np.max(D))
    threshold = dmax * (1.0 - fraction)
    pairs = []
    n = points.shape[0]
    for i in range(n):
        for j in range(i+1, n):
            if D[i, j] >= threshold - 1e-12:
                pairs.append((i, j))
    return pairs

def pca_axes(points: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Principal components axes (u1, u2)."""
    X = points - np.mean(points, axis=0)
    cov = X.T @ X
    vals, vecs = np.linalg.eigh(cov)
    order = np.argsort(vals)[::-1]
    u1 = vecs[:, order[0]]
    u2 = vecs[:, order[1]]
    # normalize
    u1 = u1 / (np.linalg.norm(u1) + 1e-12)
    u2 = u2 / (np.linalg.norm(u2) + 1e-12)
    return u1, u2


# -------------------------------
# Move operators
# -------------------------------

def rank2_squeeze(points: np.ndarray, gamma1=0.04, gamma2=0.02) -> np.ndarray:
    """Apply rank-2 anisotropic squeeze guided by PCA of active near-diameter chords."""
    # Center on MEC
    c, _ = mec_welzl(points)
    pairs = active_farthest_pairs(points, fraction=0.07)
    if len(pairs) >= 2:
        ends = np.array([points[i] for i, j in pairs] + [points[j] for i, j in pairs])
        u1, u2 = pca_axes(ends)
    else:
        u1, u2 = pca_axes(points)
    # Build affine contraction A = I − γ1 u1 u1^T − γ2 u2 u2^T
    U1 = np.outer(u1, u1)
    U2 = np.outer(u2, u2)
    A = np.eye(2) - gamma1 * U1 - gamma2 * U2
    P = points.copy()
    P = c + (P - c) @ A.T
    # Feasibility repair and normalization
    P = pocs_enforce_min_distance(P, target_min=1.0, max_iter=50, step_clip=0.2)
    return P


def gap_equalize(points: np.ndarray, beta=0.02, topk=None) -> np.ndarray:
    """Equalize angular gaps along convex hull tangentially around MEC center."""
    hull_idx = convex_hull_indices(points)
    if len(hull_idx) < 4:
        return points
    P = points.copy()
    c, r = mec_welzl(P)
    hull_pts = P[hull_idx]
    angles = np.arctan2(hull_pts[:, 1] - c[1], hull_pts[:, 0] - c[0])
    # sort hull by angle CCW
    order = np.argsort(angles)
    hull_idx = [hull_idx[i] for i in order]
    angles = angles[order]
    # unwrap angles to be increasing
    angles = np.unwrap(angles)
    gaps = np.diff(np.concatenate([angles, angles[:1] + 2 * np.pi]))
    # focus on largest gaps if requested
    if topk is not None and topk > 0 and topk < len(gaps):
        largest_idx = np.argsort(gaps)[-topk:]
        mask = np.zeros_like(gaps, dtype=bool)
        mask[largest_idx] = True
    else:
        mask = np.ones_like(gaps, dtype=bool)
    # Laplacian equalization tangential move
    n = len(hull_idx)
    delta_theta = np.zeros(n)
    for k in range(n):
        g_prev = gaps[(k - 1) % n]
        g_k = gaps[k % n]
        if mask[k] or mask[(k - 1) % n]:
            delta_theta[k] = beta * (g_prev - g_k)
    # apply tangential moves: move hull points along circle tangent, preserving radius to c
    for idx, dth in zip(hull_idx, delta_theta):
        v = P[idx] - c
        rv = np.linalg.norm(v)
        if rv < 1e-12:
            continue
        th = math.atan2(v[1], v[0])
        th_new = th + dth
        new = c + rv * np.array([math.cos(th_new), math.sin(th_new)])
        P[idx] = new
    # Repair feasibility and normalize
    P = pocs_enforce_min_distance(P, target_min=1.0, max_iter=40, step_clip=0.2)
    return P


def antipodal_pivot_swap(points: np.ndarray, delta_theta=0.015) -> np.ndarray:
    """Slightly permute antipodal hull partners by tangential moves around MEC."""
    P = points.copy()
    c, _ = mec_welzl(P)
    hull_idx = convex_hull_indices(P)
    if len(hull_idx) < 4:
        return P
    # compute farthest pairs (approx by hull max distances)
    H = P[hull_idx]
    D = pairwise_dists(H)
    # select up to two farthest pairs (distinct)
    flat_idx = np.argsort(D.ravel())[::-1]
    pairs = []
    used = set()
    for idx in flat_idx:
        i = idx // D.shape[1]
        j = idx % D.shape[1]
        if i == j:
            continue
        a, b = hull_idx[i], hull_idx[j]
        key = tuple(sorted((a, b)))
        if key in used:
            continue
        pairs.append((a, b))
        used.add(key)
        if len(pairs) >= 2:
            break
    for (i, j) in pairs:
        for idx, sgn in ((i, +1), (j, -1)):
            v = P[idx] - c
            rv = np.linalg.norm(v)
            if rv < 1e-12:
                continue
            th = math.atan2(v[1], v[0])
            th_new = th + sgn * delta_theta
            P[idx] = c + rv * np.array([math.cos(th_new), math.sin(th_new)])
    P = pocs_enforce_min_distance(P, target_min=1.0, max_iter=40, step_clip=0.2)
    return P


def mec_support_inward(points: np.ndarray, alpha=0.02) -> np.ndarray:
    """Move non-MEC-support hull points radially inward; freeze support points."""
    P = points.copy()
    c, r = mec_welzl(P)
    radii = np.linalg.norm(P - c, axis=1)
    tol = 1e-3
    support_mask = np.abs(radii - r) < tol
    for i in range(P.shape[0]):
        v = P[i] - c
        rv = np.linalg.norm(v)
        if rv < 1e-12:
            continue
        if support_mask[i]:
            continue  # freeze MEC support
        # move inward by alpha
        new_r = max(rv * (1.0 - alpha), 0.0)
        P[i] = c + (new_r / rv) * v
    P = pocs_enforce_min_distance(P, target_min=1.0, max_iter=40, step_clip=0.2)
    return P


def hull_farthest_pairs(points: np.ndarray, tol_frac: float = 0.003) -> List[Tuple[int, int]]:
    """Return list of hull vertex pairs achieving near-maximum distances within tolerance fraction."""
    hull_idx = convex_hull_indices(points)
    if len(hull_idx) < 2:
        return []
    H = points[hull_idx]
    D = pairwise_dists(H)
    dmax = float(np.max(D))
    pairs = []
    n = len(hull_idx)
    for a in range(n):
        for b in range(a+1, n):
            if D[a, b] >= dmax * (1.0 - tol_frac) - 1e-12:
                pairs.append((hull_idx[a], hull_idx[b]))
    return pairs


def calipers_active_pair_shrink(points: np.ndarray, eta=0.04) -> np.ndarray:
    """Move endpoints of near-diameter hull pairs toward their midpoints slightly."""
    P = points.copy()
    pairs = hull_farthest_pairs(P, tol_frac=0.004)
    if not pairs:
        return P
    moves = np.zeros_like(P)
    counts = np.zeros(P.shape[0], dtype=int)
    for i, j in pairs:
        mi = 0.5 * (P[i] + P[j])
        vi = mi - P[i]
        vj = mi - P[j]
        moves[i] += eta * vi
        moves[j] += eta * vj
        counts[i] += 1
        counts[j] += 1
    for k in range(P.shape[0]):
        if counts[k] > 0:
            P[k] += moves[k] / counts[k]
    P = pocs_enforce_min_distance(P, target_min=1.0, max_iter=40, step_clip=0.2)
    return P


def convex_width_scan_squeeze(points: np.ndarray, lam=0.02, dirs=12) -> np.ndarray:
    """Apply a small rank-1 contraction along the worst (max width) direction."""
    P = points.copy()
    c, _ = mec_welzl(P)
    # sample directions
    thetas = np.linspace(0.0, math.pi, num=dirs, endpoint=False)
    best_width = -1.0
    best_u = None
    X = P - c
    for th in thetas:
        u = np.array([math.cos(th), math.sin(th)], dtype=float)
        proj = X @ u
        width = float(np.max(proj) - np.min(proj))
        if width > best_width:
            best_width = width
            best_u = u
    if best_u is None:
        return P
    U = np.outer(best_u, best_u)
    A = np.eye(2) - lam * U
    P = c + (P - c) @ A.T
    P = pocs_enforce_min_distance(P, target_min=1.0, max_iter=40, step_clip=0.2)
    return P


def contact_graph_laplacian_polish(points: np.ndarray, eps=0.02, mu=0.2, steps=2) -> np.ndarray:
    """Smooth near-contact edges (1 ≤ d ≤ 1+eps) with a small constrained Laplacian step."""
    P = points.copy()
    for _ in range(steps):
        D = pairwise_dists(P)
        n = P.shape[0]
        moves = np.zeros_like(P)
        deg = np.zeros(n, dtype=float)
        for i in range(n):
            for j in range(i+1, n):
                dij = D[i, j]
                if dij >= 1.0 - 1e-9 and dij <= 1.0 + eps + 1e-12:
                    # consider near-contact; encourage even spacing
                    moves[i] += (P[j] - P[i])
                    moves[j] += (P[i] - P[j])
                    deg[i] += 1.0
                    deg[j] += 1.0
        for i in range(n):
            if deg[i] > 0:
                P[i] += mu * (moves[i] / deg[i])
        P = pocs_enforce_min_distance(P, target_min=1.0, max_iter=30, step_clip=0.2)
        P = recenter(P)
    P = pocs_enforce_min_distance(P, target_min=1.0, max_iter=40, step_clip=0.2)
    return P


# -------------------------------
# Two-ring parameter manifold exploration
# -------------------------------

def two_ring_points(k1: int, k2: int, r1: float, r2: float, phi: float) -> np.ndarray:
    """Construct two-ring configuration with radii r1, r2 and phase offset phi."""
    pts = []
    for i in range(k1):
        th = 2.0 * math.pi * i / k1
        pts.append([r1 * math.cos(th), r1 * math.sin(th)])
    for j in range(k2):
        th = 2.0 * math.pi * j / k2 + phi
        pts.append([r2 * math.cos(th), r2 * math.sin(th)])
    return np.array(pts, dtype=float)

def eval_param_ratio_squared(k1, k2, r1, r2, phi) -> float:
    """Evaluate ratio^2 for two-ring params by scaling to D_min=1 implicitly."""
    P = two_ring_points(k1, k2, r1, r2, phi)
    D = pairwise_dists(P)
    nonzero = D[D > 1e-12]
    dmin = float(np.min(nonzero))
    dmax = float(np.max(nonzero))
    if dmin <= 0:
        return float('inf')
    return (dmax / dmin) ** 2

def best_two_ring_candidate(splits: List[Tuple[int,int]], trials=256) -> Tuple[np.ndarray, float]:
    """Random-search CMA-like sampling on two-ring parameters to find low ratio^2."""
    rng = np.random.default_rng(42)
    best_R2 = float('inf')
    best_P = None
    for (k1, k2) in splits:
        # heuristic init radii and phase
        phi0 = math.pi / (2 * k2)
        r1_mean = 1.1
        r2_mean = 1.8
        cov = np.diag([0.15**2, 0.25**2, (0.4)**2])
        for _ in range(max(1, trials // max(1, len(splits)))):
            sample = rng.multivariate_normal([r1_mean, r2_mean, phi0], cov)
            r1 = float(abs(sample[0]))
            r2 = float(abs(sample[1]))
            phi = float(sample[2] % (2*math.pi))
            R2 = eval_param_ratio_squared(k1, k2, r1, r2, phi)
            if R2 < best_R2:
                P = two_ring_points(k1, k2, r1, r2, phi)
                # scale to D_min=1, recenter
                P = normalize_min_distance(P, 1.0)
                P = recenter(P)
                best_R2 = R2
                best_P = P
    return best_P, best_R2


# -------------------------------
# Seeding strategies
# -------------------------------

def seed_hex_lattice_patch(n=16) -> np.ndarray:
    """Seed points from a hexagonal lattice patch near origin and select 16 closest."""
    pts = []
    # basis vectors for hex lattice
    b1 = np.array([1.0, 0.0])
    b2 = np.array([0.5, math.sqrt(3)/2.0])
    for i in range(-3, 4):
        for j in range(-3, 4):
            p = i * b1 + j * b2
            pts.append(p)
    pts = np.array(pts, dtype=float)
    # choose 16 closest to origin
    d = np.linalg.norm(pts, axis=1)
    idx = np.argsort(d)[:n]
    P = pts[idx]
    P = recenter(P)
    P = pocs_enforce_min_distance(P, target_min=1.0, max_iter=80, step_clip=0.25)
    return P

def seed_jittered_poisson(n=16, radius=1.9, trials=400) -> np.ndarray:
    """Random jittered points within a disc, then POCS repair."""
    rng = np.random.default_rng(123)
    pts = []
    for _ in range(n):
        # random in disc of given radius
        r = radius * math.sqrt(rng.random())
        th = 2 * math.pi * rng.random()
        pts.append([r*math.cos(th), r*math.sin(th)])
    P = np.array(pts, dtype=float)
    P = recenter(P)
    P = pocs_enforce_min_distance(P, target_min=1.0, max_iter=100, step_clip=0.25)
    return P

def seed_two_ring_split(k1, k2, r1=1.2, r2=1.8, phi=None) -> np.ndarray:
    if phi is None:
        phi = math.pi / (2 * max(3, k2))
    P = two_ring_points(k1, k2, r1, r2, phi)
    P = normalize_min_distance(P, 1.0)
    P = recenter(P)
    return P

def rotate_points(P: np.ndarray, theta: float) -> np.ndarray:
    R = np.array([[math.cos(theta), -math.sin(theta)],
                  [math.sin(theta),  math.cos(theta)]], dtype=float)
    return (P @ R.T)


# -------------------------------
# Island structure and scheduler
# -------------------------------

@dataclass
class Island:
    points: np.ndarray
    ratio2: float
    engine: str
    meta: dict

def evaluate(points: np.ndarray) -> Tuple[float, float, float]:
    """Return D_min, D_max, R^2 for points."""
    D = pairwise_dists(points)
    nonzero = D[D > 1e-12]
    dmin = float(np.min(nonzero))
    dmax = float(np.max(nonzero))
    R2 = (dmax / dmin) ** 2 if dmin > 0 else float('inf')
    return dmin, dmax, R2

def accept_if_nonincrease(curr: Island, candidate_pts: np.ndarray) -> Tuple[bool, Island]:
    """Accept candidate if exact D_max (with D_min normalized to 1) does not increase."""
    # normalize D_min to 1 for both configurations
    cand = normalize_min_distance(candidate_pts, 1.0)
    cand = recenter(cand)
    _, dmax_old, _ = evaluate(normalize_min_distance(curr.points, 1.0))
    _, dmax_new, _ = evaluate(cand)
    if dmax_new <= dmax_old + 1e-9:
        new_R2 = dmax_new**2
        return True, Island(points=cand, ratio2=new_R2, engine=curr.engine, meta=curr.meta)
    return False, curr

class BanditScheduler:
    def __init__(self, moves: List[str], init_weight=1.0):
        self.moves = moves
        self.weights = {m: init_weight for m in moves}
        self.success = {m: 0 for m in moves}
        self.attempts = {m: 1 for m in moves}  # avoid div by zero
        self.epsilon = 0.1  # small exploration
    def record(self, move: str, success: bool):
        self.attempts[move] += 1
        if success:
            self.success[move] += 1
            # boost weight modestly
            self.weights[move] *= 1.03
        else:
            # slight decay to encourage exploration elsewhere
            self.weights[move] *= 0.995
    def choose(self) -> str:
        # Epsilon-greedy with weights as preferences
        moves = self.moves
        if random.random() < self.epsilon:
            return random.choice(moves)
        # sample proportional to weights
        w = np.array([self.weights[m] for m in moves], dtype=float)
        w = w / (np.sum(w) + 1e-12)
        return random.choices(moves, weights=w, k=1)[0]


# -------------------------------
# Main optimize function
# -------------------------------

def optimize_construct(n=16, d=2):
    if (n, d) != (16, 2):
        raise ValueError("the benchmark evaluates n=16 and d=2")

    rng = np.random.default_rng(20260831)
    random.seed(20260831)

    # Initialize diverse islands
    islands: List[Island] = []

    # Seed 1: hex lattice patch near origin
    P_hex = seed_hex_lattice_patch(n)
    islands.append(Island(points=P_hex, ratio2=objective_ratio_squared(P_hex), engine='A', meta={}))

    # Seed 2: jittered Poisson
    P_jit = seed_jittered_poisson(n=n, radius=1.95)
    islands.append(Island(points=P_jit, ratio2=objective_ratio_squared(P_jit), engine='B', meta={}))

    # Seed 3: rotated hex lattice
    P_hex_rot = rotate_points(P_hex, theta=math.pi/7.0)
    P_hex_rot = pocs_enforce_min_distance(P_hex_rot, target_min=1.0, max_iter=60)
    islands.append(Island(points=P_hex_rot, ratio2=objective_ratio_squared(P_hex_rot), engine='A', meta={}))

    # Two-ring seeds across splits
    splits = [(8,8), (6,10), (7,9), (5,11)]
    for (k1, k2) in splits:
        P_ring = seed_two_ring_split(k1, k2, r1=1.15, r2=1.85, phi=math.pi/(2*k2))
        islands.append(Island(points=P_ring, ratio2=objective_ratio_squared(P_ring), engine='C', meta={'k1':k1,'k2':k2}))

    # Best param candidate from random search
    P_best_param, best_R2_param = best_two_ring_candidate(splits, trials=256)
    if P_best_param is not None:
        islands.append(Island(points=P_best_param, ratio2=best_R2_param, engine='C', meta={'param_best': True}))

    # Additional jittered seeds
    for _ in range(4):
        P_rand = seed_jittered_poisson(n=n, radius=1.85 + 0.2 * rng.random())
        islands.append(Island(points=P_rand, ratio2=objective_ratio_squared(P_rand), engine=random.choice(['A','B']), meta={}))

    # Move set and bandit scheduler
    move_set = [
        'rank2_squeeze',
        'gap_equalize',
        'antipodal_pivot',
        'mec_inward',
        'calipers_shrink',
        'width_scan',
        'contact_polish',
        'ring_manifold',
    ]
    scheduler = BanditScheduler(move_set, init_weight=1.0)

    # Global elite archive
    elite_points = None
    elite_ratio2 = float('inf')

    def update_elite(isle: Island):
        nonlocal elite_points, elite_ratio2
        if isle.ratio2 < elite_ratio2:
            elite_ratio2 = isle.ratio2
            elite_points = isle.points.copy()

    for isle in islands:
        update_elite(isle)

    # Helper: propose a candidate with a given move and scale factor
    def propose_move(points: np.ndarray, move: str, scale: float, it: int) -> np.ndarray:
        if move == 'rank2_squeeze':
            # Anneal gammas slightly with iteration
            g1 = 0.04 * scale
            g2 = 0.02 * scale
            return rank2_squeeze(points, gamma1=g1, gamma2=g2)
        elif move == 'gap_equalize':
            beta = 0.02 * scale
            topk = 3 if it % 2 == 0 else None
            return gap_equalize(points, beta=beta, topk=topk)
        elif move == 'antipodal_pivot':
            dth = 0.02 * scale
            return antipodal_pivot_swap(points, delta_theta=dth)
        elif move == 'mec_inward':
            alpha = 0.025 * scale
            return mec_support_inward(points, alpha=alpha)
        elif move == 'calipers_shrink':
            eta = 0.05 * scale
            return calipers_active_pair_shrink(points, eta=eta)
        elif move == 'width_scan':
            lam = 0.02 * scale
            return convex_width_scan_squeeze(points, lam=lam, dirs=14)
        elif move == 'contact_polish':
            # small steps of Laplacian polish
            eps = 0.02
            mu = 0.18 * scale
            return contact_graph_laplacian_polish(points, eps=eps, mu=mu, steps=2)
        elif move == 'ring_manifold':
            # ring manifold is scale-less here; handled in main branch
            return points.copy()
        else:
            return points.copy()

    # Optimization loop
    max_outer_iters = 800
    migrate_period = 60

    for it in range(max_outer_iters):
        # Select island biased to worse performers for attention
        ratios = np.array([isle.ratio2 for isle in islands], dtype=float)
        # pick one island at random but biased toward worse
        probs = np.maximum(ratios - np.min(ratios), 1e-9)
        probs = probs / (np.sum(probs) + 1e-12)
        sel_idx = int(np.random.choice(len(islands), p=probs))
        curr = islands[sel_idx]

        # Choose a move
        move = scheduler.choose()

        # Apply move with backtracking and acceptance
        success = False
        new_isle = curr
        candidate = curr.points.copy()

        if move == 'ring_manifold':
            # Propose param candidate around split from meta or random split
            if curr.engine == 'C' and 'k1' in curr.meta and 'k2' in curr.meta:
                k1 = curr.meta['k1']
                k2 = curr.meta['k2']
            else:
                k1, k2 = random.choice(splits)
            # Derive approximate radii from current diameter
            _, dmax_curr, _ = evaluate(normalize_min_distance(curr.points, 1.0))
            r2_guess = 0.5 * dmax_curr * (0.95 + 0.1 * np.random.random())
            r1_guess = r2_guess * (0.6 + 0.35 * np.random.random())
            phi_guess = math.pi / (2 * k2) * (0.6 + 0.8 * np.random.random())
            # Sample nearby candidates
            best_cand = None
            best_R2 = float('inf')
            for _ in range(16):
                r1 = abs(r1_guess + np.random.normal(0, 0.12))
                r2 = abs(r2_guess + np.random.normal(0, 0.12))
                phi = (phi_guess + np.random.normal(0, 0.3)) % (2 * math.pi)
                R2 = eval_param_ratio_squared(k1, k2, r1, r2, phi)
                if R2 < best_R2:
                    best_R2 = R2
                    Pp = two_ring_points(k1, k2, r1, r2, phi)
                    Pp = normalize_min_distance(Pp, 1.0)
                    Pp = recenter(Pp)
                    best_cand = Pp
            if best_cand is None:
                best_cand = candidate
            accepted, new_isle = accept_if_nonincrease(curr, best_cand)
            success = accepted
        else:
            # Backtracking over scale
            scales = [1.0, 0.7, 0.5, 0.35, 0.25, 0.18, 0.12]
            for sc in scales:
                cand = propose_move(candidate, move, sc, it)
                accepted, tmp_isle = accept_if_nonincrease(curr, cand)
                if accepted:
                    success = True
                    new_isle = tmp_isle
                    break

        scheduler.record(move, success)

        # Update island and elite if accepted
        if success:
            islands[sel_idx] = new_isle
            update_elite(new_isle)

        # Migration and restarts
        if (it + 1) % migrate_period == 0:
            # Archive elite and reseed worst 2 islands with perturbations/crossover
            if elite_points is not None:
                # Worst islands indices
                worst_indices = np.argsort([isle.ratio2 for isle in islands])[::-1][:2]
                for wi in worst_indices:
                    choice = random.choice(['perturb', 'topo_restart', 'crossover'])
                    if choice == 'perturb':
                        # Small rotation + noise + POCS
                        theta = np.random.uniform(-0.25, 0.25)
                        Pnew = rotate_points(elite_points, theta)
                        Pnew += np.random.normal(0, 0.03, size=Pnew.shape)
                        Pnew = pocs_enforce_min_distance(Pnew, target_min=1.0, max_iter=60)
                        islands[wi] = Island(points=Pnew, ratio2=objective_ratio_squared(Pnew),
                                             engine=random.choice(['A','B']), meta={})
                    elif choice == 'topo_restart':
                        # New ring-manifold seed with different split
                        k1, k2 = random.choice(splits)
                        Pnew = seed_two_ring_split(k1, k2, r1=1.15 + 0.2*np.random.random(), r2=1.85 + 0.25*np.random.random(),
                                                   phi=math.pi/(2*k2)*(0.5 + np.random.random()))
                        islands[wi] = Island(points=Pnew, ratio2=objective_ratio_squared(Pnew),
                                             engine='C', meta={'k1':k1,'k2':k2})
                    else:
                        # crossover: half points from two elites (elite + another good island)
                        other_idx = int(np.argmin([isle.ratio2 for isle in islands]))
                        P_a = elite_points.copy()
                        P_b = islands[other_idx].points.copy()
                        idx_a = np.random.choice(n, size=n//2, replace=False)
                        mask_a = np.zeros(n, dtype=bool); mask_a[idx_a] = True
                        Pnew = np.where(mask_a[:,None], P_a, P_b)
                        # small rotation and repair
                        Pnew = rotate_points(Pnew, np.random.uniform(-0.2, 0.2))
                        Pnew = pocs_enforce_min_distance(Pnew, target_min=1.0, max_iter=80)
                        islands[wi] = Island(points=Pnew, ratio2=objective_ratio_squared(Pnew),
                                             engine=random.choice(['A','B','C']), meta={})

    # Final polish loop on elite
    if elite_points is None:
        # fallback: return hex lattice
        P_out = P_hex
        R2_out = objective_ratio_squared(P_out)
        return P_out, float(R2_out)

    P_polish = elite_points.copy()
    # Tight monotone polish iterations with backtracking
    polish_moves = ['calipers_shrink', 'gap_equalize', 'rank2_squeeze', 'mec_inward', 'width_scan', 'contact_polish', 'antipodal_pivot']
    for itp in range(140):
        curr = Island(points=P_polish, ratio2=objective_ratio_squared(P_polish), engine='A', meta={})
        for mv in polish_moves:
            scales = [0.8, 0.6, 0.45, 0.32, 0.22, 0.16]
            moved = False
            for sc in scales:
                if mv == 'rank2_squeeze':
                    cand = rank2_squeeze(P_polish, gamma1=0.03*sc, gamma2=0.015*sc)
                elif mv == 'gap_equalize':
                    cand = gap_equalize(P_polish, beta=0.015*sc, topk=2)
                elif mv == 'mec_inward':
                    cand = mec_support_inward(P_polish, alpha=0.02*sc)
                elif mv == 'antipodal_pivot':
                    cand = antipodal_pivot_swap(P_polish, delta_theta=0.015*sc)
                elif mv == 'calipers_shrink':
                    cand = calipers_active_pair_shrink(P_polish, eta=0.04*sc)
                elif mv == 'width_scan':
                    cand = convex_width_scan_squeeze(P_polish, lam=0.015*sc, dirs=16)
                elif mv == 'contact_polish':
                    cand = contact_graph_laplacian_polish(P_polish, eps=0.02, mu=0.15*sc, steps=2)
                else:
                    cand = P_polish
                acc, new_isle = accept_if_nonincrease(curr, cand)
                if acc:
                    P_polish = new_isle.points
                    curr = new_isle
                    moved = True
                    break
            # if no scale accepted, skip to next move
            _ = moved

    P_out = normalize_min_distance(P_polish, 1.0)
    P_out = recenter(P_out)
    R2_out = objective_ratio_squared(P_out)
    return P_out, float(R2_out)

# EVOLVE_END


if __name__ == "__main__":
    points, _ = optimize_construct(16, 2)
    print(json.dumps({"points": points.tolist()}))
```
