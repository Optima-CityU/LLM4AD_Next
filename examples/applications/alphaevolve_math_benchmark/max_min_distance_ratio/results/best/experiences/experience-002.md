Design choices and reusable strategies that led to effective performance on the constrained minimax objective with D_min maintained at 1.

- Minimax Compress-and-Project with Hex-Seeds (MCPS): For n=16 and d=2, MCPS maintains D_min = 1 via alternating projection and uniform rescaling, converting the cost to minimizing D_max^2, and applies minimax-aware compression toward the midpoint of the current farthest pair with an explicit pullback on that pair to directly reduce the diameter.
- Minimax Compress-and-Project with Hex-Seeds (MCPS): MCPS enforces feasibility through a repulsive barrier on subunit pairs while seeding the search with diverse hexagonal lattice patches, Poisson-disk samples, and ring prototypes, then finishes with a derivative-free trust-region (Hooke–Jeeves style) coordinate polishing step.
- In the reported run for n=16, d=2, optimize_construct achieved ratio_squared = 13.000000005885749 with validity = 1.0 in 51.45515300799161 seconds, evidencing that the pipeline preserved min-distance constraints while targeting the worst-pair diameter.

```python
#!/usr/bin/env python3
"""Search for 16 planar points minimizing the squared ratio of max/min distances.

Implements a targeted compress-and-project dynamics with multi-start seeding and
a final local polishing step. We enforce D_min = 1 throughout (by projection+
rescaling), so the objective reduces to minimizing the squared diameter D_max^2.
"""

import json
from typing import Tuple, List

import numpy as np


# EVOLVE_START
def optimize_construct(n=16, d=2):
    """
    Main entrypoint used by the benchmark.
    Returns:
      - points: (n, d) array with the final coordinates
      - ratio_squared: float, equal to (D_max / D_min)^2. We keep D_min = 1, so this is D_max^2.
    """
    if (n, d) != (16, 2):
        raise ValueError("the benchmark evaluates n=16 and d=2")

    rng = np.random.default_rng()

    # Parameters for the relax-and-project optimizer
    T_iters = 1500            # iterations per seed
    seeds_count = 36          # number of initial seeds
    alpha = 0.22              # compression toward center
    beta = 4.0                # barrier coefficient
    barrier_power = 3.0       # barrier exponent m
    gamma = 0.45              # extra pull on farthest pair
    eta0 = 0.2                # initial step size
    eta_min = 1e-4
    eta_max = 0.35
    accept_tol = 1e-9
    patience_noise = 90       # stagnant steps before noise
    noise_sigma0 = 0.05       # initial noise amplitude
    noise_sigma_min = 0.005

    # Generate diverse seeds
    seeds = []
    seeds.extend(hex_rhombus_seeds(16, rotations=12, rng=rng))    # hex lattice rhombi
    seeds.extend(hex_disc_seeds(16, variants=12, rng=rng))        # nearest-16 disc on hex lattice
    seeds.extend(poisson_seeds(16, count=6, rng=rng))             # Poisson-disk like
    seeds.extend(rings_seeds(16, count=6, rng=rng))               # concentric rings

    # Limit number of seeds if more generated
    if len(seeds) > seeds_count:
        seeds = rng.choice(seeds, size=seeds_count, replace=False).tolist()

    # Normalize all seeds to have D_min = 1 initially
    seeds = [normalize_min_distance(s) for s in seeds]

    best_points = None
    best_d2 = np.inf

    # Run relaxation on each seed
    for seed in seeds:
        pts = seed.copy()
        pts = recenter(pts)

        # Ensure feasibility
        pts = project_min_distance(pts, min_dist=1.0, max_passes=8)
        pts = normalize_min_distance(pts)

        # Initial stats
        dmin, dmax, (ai, aj) = stats_dmin_dmax(pts)
        current_best_d2 = dmax * dmax
        best_seed_pts = pts.copy()
        stagnant = 0
        eta = eta0
        sigma = noise_sigma0

        for t in range(T_iters):
            # Compute farthest pair and "center" c as their midpoint (approx MEC center)
            _, _, (fi, fj) = stats_dmin_dmax(pts)
            pa, pb = pts[fi], pts[fj]
            c = 0.5 * (pa + pb)

            # Compute velocities from compression and barrier
            v = -alpha * (pts - c)

            # Barrier repulsion for pairs with r < 1
            # Also collect the farthest-pair correction
            npts = pts.shape[0]
            for i in range(npts):
                for j in range(i + 1, npts):
                    diff = pts[i] - pts[j]
                    r = np.linalg.norm(diff)
                    if r < 1.0 and r > 1e-12:
                        u = diff / r
                        mag = beta * ((1.0 - r) ** barrier_power)
                        v[i] += mag * u
                        v[j] -= mag * u

            # Farthest pair pullback
            mid = 0.5 * (pa + pb)
            v[fi] += -gamma * (pa - mid)
            v[fj] += -gamma * (pb - mid)

            # Try update with simple line-search style backtracking
            accepted = False
            pts_prev = pts.copy()
            dmax_prev = stats_dmin_dmax(pts_prev)[1]

            tries = 0
            while tries < 6:
                trial = pts_prev + eta * v
                trial = project_min_distance(trial, min_dist=1.0, max_passes=6)
                trial = normalize_min_distance(trial)
                # Recentering to prevent drift
                trial = recenter(trial)

                dmin_t, dmax_t, _ = stats_dmin_dmax(trial)
                if dmin_t < 1.0 - 1e-7:
                    # Numerical slip; reject
                    accepted = False
                else:
                    if dmax_t <= dmax_prev + accept_tol:
                        accepted = True
                        pts = trial
                        break
                # backtrack
                eta *= 0.5
                tries += 1

            if not accepted:
                # reject and add a small jitter to escape flat regions
                pts = pts_prev + rng.normal(scale=1e-4, size=pts_prev.shape)

            # If accepted, mildly increase step size; else keep reduced eta
            if accepted:
                eta = min(eta * 1.05, eta_max)
            else:
                eta = max(eta, eta_min)

            # Track best for this seed
            dmin, dmax, _ = stats_dmin_dmax(pts)
            d2 = dmax * dmax
            if d2 + 1e-12 < current_best_d2:
                current_best_d2 = d2
                best_seed_pts = pts.copy()
                stagnant = 0
            else:
                stagnant += 1

            # Annealed noise if stagnant
            if stagnant >= patience_noise:
                sigma = max(sigma * 0.75, noise_sigma_min)
                pts += rng.normal(scale=sigma, size=pts.shape)
                pts = project_min_distance(pts, min_dist=1.0, max_passes=6)
                pts = normalize_min_distance(pts)
                pts = recenter(pts)
                stagnant = 0

        # Local polishing with derivative-free coordinate search
        polished_pts, polished_d2 = coordinate_polish(best_seed_pts, start_radius=0.12, rng=rng)
        if polished_d2 + 1e-12 < current_best_d2:
            best_seed_pts, current_best_d2 = polished_pts, polished_d2

        # Update global best
        if current_best_d2 + 1e-12 < best_d2:
            best_d2 = current_best_d2
            best_points = best_seed_pts.copy()

    # Return the best found
    return best_points, float(best_d2)


# ------------------------ Utility and Helper Functions ------------------------ #

def pairwise_dists(points: np.ndarray) -> np.ndarray:
    """Compute pairwise Euclidean distances for points (n,2)."""
    diff = points[:, None, :] - points[None, :, :]
    return np.linalg.norm(diff, axis=-1)


def stats_dmin_dmax(points: np.ndarray) -> Tuple[float, float, Tuple[int, int]]:
    """Return (dmin, dmax, (i,j) for farthest pair)."""
    D = pairwise_dists(points)
    n = D.shape[0]
    # Exclude self distances
    D_no_diag = D + np.eye(n) * 1e9
    dmin = float(np.min(D_no_diag))
    # Farthest pair
    # We can include diagonal since they are 0 and won't be maxima
    idx = int(np.argmax(D))
    i, j = np.unravel_index(idx, D.shape)
    dmax = float(D[i, j])
    return dmin, dmax, (i, j)


def recenter(points: np.ndarray) -> np.ndarray:
    """Translate so centroid is at origin."""
    c = np.mean(points, axis=0, keepdims=True)
    return points - c


def normalize_min_distance(points: np.ndarray, target: float = 1.0) -> np.ndarray:
    """Uniformly rescale so that the minimum pairwise distance equals target."""
    dmin, _, _ = stats_dmin_dmax(points)
    if dmin <= 0:
        return points.copy()
    scale = target / dmin
    return points * scale


def project_min_distance(points: np.ndarray, min_dist: float = 1.0, max_passes: int = 8) -> np.ndarray:
    """Alternating projection to enforce that all pairwise distances >= min_dist.

    Performs several passes; in each pass, for any violating pair, pushes both points
    symmetrically along their connecting line to set their distance to min_dist.
    """
    pts = points.copy()
    n = pts.shape[0]
    if n <= 1:
        return pts

    for _ in range(max_passes):
        D = pairwise_dists(pts)
        violated = []
        # Collect violating pairs
        for i in range(n):
            for j in range(i + 1, n):
                dij = D[i, j]
                if dij < min_dist - 1e-12:
                    violated.append((dij, i, j))
        if not violated:
            break
        # Resolve most severe first
        violated.sort(key=lambda x: x[0])
        for dij, i, j in violated:
            diff = pts[i] - pts[j]
            r = np.linalg.norm(diff)
            if r < 1e-12:
                # Coincident; separate in random small direction
                direction = np.array([1.0, 0.0])
            else:
                direction = diff / r
            delta = (min_dist - r) * 0.5
            pts[i] += direction * delta
            pts[j] -= direction * delta
    return pts


# ------------------------ Seeding Strategies ------------------------ #

def hex_rhombus_seeds(n: int, rotations: int = 8, rng: np.random.Generator = None) -> List[np.ndarray]:
    """Create rhombic patches of a triangular lattice with several random rotations."""
    if rng is None:
        rng = np.random.default_rng()
    seeds = []
    # 4x4 rhombus -> 16 points exactly
    size = 4
    base = []
    for j in range(size):
        for i in range(size):
            x = i + 0.5 * (j % 2)
            y = (np.sqrt(3) / 2.0) * j
            base.append([x, y])
    base = np.array(base, dtype=float)
    base -= np.mean(base, axis=0, keepdims=True)
    # Rotate multiple orientations
    for _ in range(rotations):
        theta = rng.uniform(0, 2 * np.pi)
        R = rotation_matrix(theta)
        pts = base @ R.T
        pts = normalize_min_distance(pts)
        seeds.append(pts)
    return seeds


def hex_disc_seeds(n: int, variants: int = 8, rng: np.random.Generator = None) -> List[np.ndarray]:
    """Select 16 closest-to-origin points from a triangular lattice, several orientations."""
    if rng is None:
        rng = np.random.default_rng()
    seeds = []
    # Build a reasonably big triangular lattice around origin
    L = 5
    coords = []
    for j in range(-L, L + 1):
        for i in range(-L, L + 1):
            x = i + 0.5 * (j & 1)
            y = (np.sqrt(3) / 2.0) * j
            coords.append([x, y])
    coords = np.array(coords, dtype=float)

    for _ in range(variants):
        theta = rng.uniform(0, 2 * np.pi)
        R = rotation_matrix(theta)
        pts = coords @ R.T
        # pick 16 closest to origin
        r = np.sum(pts**2, axis=1)
        idx = np.argsort(r)[:n]
        subset = pts[idx]
        subset = normalize_min_distance(subset)
        subset = recenter(subset)
        seeds.append(subset)
    return seeds


def poisson_seeds(n: int, count: int = 6, rng: np.random.Generator = None) -> List[np.ndarray]:
    """Simple dart throwing in a square with min spacing ~1; scaled and centered."""
    if rng is None:
        rng = np.random.default_rng()
    seeds = []
    for _ in range(count):
        pts = []
        L = 6.0
        attempts = 0
        while len(pts) < n and attempts < 10000:
            candidate = rng.uniform(low=-L, high=L, size=2)
            ok = True
            for p in pts:
                if np.linalg.norm(candidate - p) < 0.98:  # a bit below 1; we'll project after
                    ok = False
                    break
            if ok:
                pts.append(candidate)
            attempts += 1
        if len(pts) < n:
            # Fallback: random gaussian cloud
            pts = rng.normal(size=(n, 2))
        pts = np.array(pts, dtype=float)
        pts = project_min_distance(pts, min_dist=1.0, max_passes=8)
        pts = normalize_min_distance(pts)
        pts = recenter(pts)
        seeds.append(pts)
    return seeds


def rings_seeds(n: int, count: int = 6, rng: np.random.Generator = None) -> List[np.ndarray]:
    """Two rings of 8 points each; inner/outer radii chosen from chord = 1 for 8-gon."""
    if rng is None:
        rng = np.random.default_rng()
    seeds = []
    for _ in range(count):
        # 8 + 8 = 16
        k = 8
        # Ensure adjacent chord length on inner ring equals 1
        r1 = 1.0 / (2.0 * np.sin(np.pi / k))  # chord length c=1 -> r = 1/(2 sin(pi/k))
        r2 = r1 + 1.0  # outer ring roughly at +1 radial spacing
        theta0 = rng.uniform(0, 2 * np.pi)
        theta1 = theta0 + np.pi / k  # offset outer ring
        inner = np.stack([r1 * np.cos(theta0 + 2 * np.pi * i / k) for i in range(k)], axis=0)
        inner = np.c_[inner, [r1 * np.sin(theta0 + 2 * np.pi * i / k) for i in range(k)]]
        outer = np.stack([r2 * np.cos(theta1 + 2 * np.pi * i / k) for i in range(k)], axis=0)
        outer = np.c_[outer, [r2 * np.sin(theta1 + 2 * np.pi * i / k) for i in range(k)]]
        pts = np.vstack([inner, outer])
        pts = project_min_distance(pts, min_dist=1.0, max_passes=8)
        pts = normalize_min_distance(pts)
        pts = recenter(pts)
        seeds.append(pts)
    return seeds


def rotation_matrix(theta: float) -> np.ndarray:
    c = np.cos(theta)
    s = np.sin(theta)
    return np.array([[c, -s], [s, c]], dtype=float)


# ------------------------ Local Polishing ------------------------ #

def coordinate_polish(points: np.ndarray, start_radius: float = 0.12, rng: np.random.Generator = None) -> Tuple[np.ndarray, float]:
    """Simple Hooke–Jeeves style coordinate search to polish the diameter.

    Returns optimized points and D_max^2.
    """
    if rng is None:
        rng = np.random.default_rng()
    pts = points.copy()
    pts = project_min_distance(pts, min_dist=1.0, max_passes=10)
    pts = normalize_min_distance(pts)
    pts = recenter(pts)
    _, dmax, _ = stats_dmin_dmax(pts)
    best_d2 = dmax * dmax

    rho = start_radius
    for _ in range(5):  # up to 5 global sweeps with shrinking radius
        improved_any = False
        for i in range(pts.shape[0]):
            for direction in [(rho, 0.0), (-rho, 0.0), (0.0, rho), (0.0, -rho)]:
                trial = pts.copy()
                trial[i, 0] += direction[0]
                trial[i, 1] += direction[1]
                trial = project_min_distance(trial, min_dist=1.0, max_passes=8)
                trial = normalize_min_distance(trial)
                trial = recenter(trial)
                _, dmax_t, _ = stats_dmin_dmax(trial)
                d2 = dmax_t * dmax_t
                if d2 + 1e-12 < best_d2:
                    pts = trial
                    best_d2 = d2
                    improved_any = True
        if not improved_any:
            rho *= 0.5
            if rho < 1e-3:
                break
    return pts, best_d2
# EVOLVE_END


if __name__ == "__main__":
    points, _ = optimize_construct(16, 2)
    print(json.dumps({"points": points.tolist()}))
```
