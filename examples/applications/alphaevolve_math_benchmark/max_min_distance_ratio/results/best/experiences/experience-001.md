Insight on design choices and reusable strategies that contributed to PMD-Hex’s strong performance on the n=16, d=2 task.

- PMD-Hex: Projected Max-Pair Descent with Hex-Lattice Seeding: On the n=16, d=2 task, PMD-Hex normalizes D_min to 1 each iteration, uses a log-sum-exp smooth-max to shrink the farthest pair distance, and projects pairs with distance < 1, producing a valid configuration (validity 1.0) with ratio_squared 12.906813711241831 and score 0.9986404391018251 in eval_time 5.921515844995156; future designs should reuse this D_min=1 normalization + smooth-max farthest-pair descent + projection loop to target D_max under a hard minimum-distance constraint and pair it with symmetry-aware hex-lattice seeds plus jittered elite recombination to maintain diversity while avoiding square-grid local traps.
- Hex-Sector Seeds with Ratio-Projection Local Search: Normalizing D_min to 1 and directly shrinking the diameter by seeding from a compact sector of a unit-spacing hexagonal lattice, pruning extremes via boundary-point swaps, and polishing with projection-based descent on a softmax diameter surrogate (with increasing temperature) produced a strong configuration. This discrete-then-continuous pipeline avoids opposite extremes and maintains feasibility via pairwise clamping and rescaling, achieving ratio_squared 12.937100045248119 with validity 1.0, compared to a square-grid baseline with D_max^2 ≈ 18 for n = 16. For similar fixed-min-distance objectives, reuse this pattern: fix D_min = 1, beam-search compact hex-lattice sector footprints over widths/orientations, then apply hard-projection polishing on the softmax diameter with an upward τ schedule.

```python
#!/usr/bin/env python3
"""16-point planar construction minimizing max/min distance ratio via farthest-pair descent.

Implements a multi-start, projection-based optimization targeting the squared diameter
under a hard minimum-distance constraint. Seeds include hex-lattice subsets,
concentric rings, Poisson-disk samples, and golden-angle spirals. Each candidate
is refined by alternating:
  - normalization to enforce D_min = 1 and centroid at origin
  - smooth-max (log-sum-exp) descent focused on shrinking the farthest pair
  - projection resolving any unit-distance violations (push pairs apart)
An outer evolutionary loop maintains diversity with jittered elites and fresh seeds.

Returns:
  points: np.ndarray of shape (16, 2) with optimized point coordinates
  ratio_squared: float value of (D_max / D_min)^2

"""

import json
import math
import numpy as np


# EVOLVE_START
def optimize_construct(n=16, d=2):
    if (n, d) != (16, 2):
        raise ValueError("the benchmark evaluates n=16 and d=2")

    rng = np.random.default_rng(0)

    def pairwise_sqdist(P):
        """Compute squared Euclidean distances matrix for points P (n, 2)."""
        # Using (x - y)^2 via broadcasting: ||P_i - P_j||^2
        diff = P[:, None, :] - P[None, :, :]
        D2 = np.einsum("ijk,ijk->ij", diff, diff)
        return D2

    def compute_ratio_squared(P):
        """Compute (D_max / D_min)^2 using Euclidean distances."""
        D2 = pairwise_sqdist(P)
        # Mask out diagonal
        mask = ~np.eye(len(P), dtype=bool)
        nonzero = D2[mask]
        dmin2 = nonzero.min()
        dmax2 = nonzero.max()
        return float(dmax2 / dmin2)

    def normalize_min_distance(P, target=1.0):
        """Scale points so that minimum pairwise distance equals target, and center centroid."""
        D2 = pairwise_sqdist(P)
        mask = ~np.eye(len(P), dtype=bool)
        dmin2 = D2[mask].min()
        # Guard against degenerate cases
        if dmin2 <= 0:
            # Add tiny jitter to break exact coincidences
            P = P + rng.normal(scale=1e-6, size=P.shape)
            D2 = pairwise_sqdist(P)
            dmin2 = D2[mask].min()
            if dmin2 <= 0:
                dmin2 = 1e-8
        scale = target / math.sqrt(dmin2)
        P = P * scale
        # Center the centroid to stabilize optimization
        P = P - P.mean(axis=0, keepdims=True)
        return P

    def project_min_distance(P, min_d=1.0, max_passes=6, tol=1e-8):
        """Project points to satisfy ||pi - pj|| >= min_d by pushing violating pairs apart."""
        n = len(P)
        # Iterate a few passes to resolve violations
        for _ in range(max_passes):
            D2 = pairwise_sqdist(P)
            # Identify violating pairs
            viol_mask = (~np.eye(n, dtype=bool)) & (D2 < (min_d - tol) ** 2)
            if not np.any(viol_mask):
                break
            corr = np.zeros_like(P)
            # Process only upper triangle to avoid double counting
            rows, cols = np.where(np.triu(viol_mask, k=1))
            for i, j in zip(rows, cols):
                dij2 = D2[i, j]
                dij = math.sqrt(max(dij2, 0.0))
                # Compute correction magnitude along the line connecting i and j
                need = (min_d - dij) / 2.0
                if dij > 1e-12:
                    # Unit direction from j to i
                    u = (P[i] - P[j]) / dij
                else:
                    # If coincident or extremely close, pick a random unit direction
                    theta = rng.uniform(0.0, 2.0 * math.pi)
                    u = np.array([math.cos(theta), math.sin(theta)], dtype=np.float64)
                cvec = need * u
                corr[i] += cvec
                corr[j] -= cvec
            P = P + corr
            # Recenter after correction
            P = P - P.mean(axis=0, keepdims=True)
        return P

    def smooth_max_gradient(P, beta):
        """Compute gradient of f = (1/beta) * log sum_{i<j} exp(beta * ||pi-pj||^2)."""
        n = len(P)
        D2 = pairwise_sqdist(P)
        # Zero out diagonal
        np.fill_diagonal(D2, 0.0)
        # Log-sum-exp stabilization: subtract max to avoid overflow
        max_D2 = D2.max()
        # Symmetric weights for unordered pairs (W_ii = 0)
        W = np.exp(beta * (D2 - max_D2))
        np.fill_diagonal(W, 0.0)
        # Z is sum over unordered pairs; here W sums over both (i,j) and (j,i),
        # so we take Z as half the total sum to represent unordered pairs.
        Z = 0.5 * W.sum()
        if Z <= 1e-16:
            # Degenerate case: return zero gradient
            return np.zeros_like(P)
        # Gradient: g_i = (2/Z) * sum_j W_ij * (pi - pj)
        row_sums = W.sum(axis=1)  # (n,)
        g = (2.0 / Z) * (P * row_sums[:, None] - W @ P)
        return g

    def refine_candidate(P_init, steps=200, beta_start=12.0, beta_max=160.0, jitter_schedule=True):
        """Refine a candidate configuration via alternating normalization, descent, and projection."""
        P = P_init.copy()
        # Normalize initial configuration
        P = normalize_min_distance(P, 1.0)
        # Initialize variables
        eta = 0.08
        momentum = 0.85
        v = np.zeros_like(P)
        beta = beta_start
        best_P = P.copy()
        best_ratio2 = compute_ratio_squared(P)
        no_improve = 0

        for t in range(steps):
            # Ensure min distance and centering
            P = normalize_min_distance(P, 1.0)

            # Current objective (since normalized -> ratio_squared = D_max^2)
            current_ratio2 = compute_ratio_squared(P)

            # Smooth-max gradient wrt squared distances
            g = smooth_max_gradient(P, beta)

            # Propose step with momentum
            v_new = momentum * v - eta * g
            P_prop = P + v_new

            # Projection to satisfy min distance >= 1
            P_prop = project_min_distance(P_prop, 1.0, max_passes=6)
            P_prop = normalize_min_distance(P_prop, 1.0)

            new_ratio2 = compute_ratio_squared(P_prop)

            if new_ratio2 <= current_ratio2 - 1e-9:
                # Accept improvement
                P = P_prop
                v = v_new
                eta = min(0.2, eta * 1.03)  # slight increase to accelerate
                no_improve = 0
                # Track best
                if new_ratio2 < best_ratio2:
                    best_ratio2 = new_ratio2
                    best_P = P.copy()
            else:
                # Backtrack: reduce step size and damp momentum
                eta = max(1e-4, eta * 0.5)
                v = v * 0.5
                no_improve += 1
                # Occasionally accept slight non-improving move plus jitter to escape shallow traps
                if no_improve >= 5:
                    P = P_prop
                    v = v_new
                    no_improve = 0
                    eta = max(1e-3, eta)  # maintain reasonable step size

            # Anneal beta to sharpen approximation to max distance
            if (t + 1) % 25 == 0:
                beta = min(beta_max, beta * 1.25)

            # Scheduled small jitter with decreasing variance to maintain diversity
            if jitter_schedule and (t + 1) % 30 == 0:
                sigma = 0.02 / (1.0 + 0.05 * t)
                P = P + rng.normal(scale=sigma, size=P.shape)
                P = project_min_distance(P, 1.0, max_passes=4)
                P = normalize_min_distance(P, 1.0)
                # Update best if improved
                r2 = compute_ratio_squared(P)
                if r2 < best_ratio2:
                    best_ratio2 = r2
                    best_P = P.copy()

        # Return best encountered during refinement
        best_P = normalize_min_distance(best_P, 1.0)
        best_ratio2 = compute_ratio_squared(best_P)
        return best_P, best_ratio2

    # Seed generators
    def hex_lattice_seed(include_origin=True):
        """Triangular lattice with unit nearest-neighbor distance; pick 16 closest to origin."""
        b1 = np.array([1.0, 0.0])
        b2 = np.array([0.5, math.sqrt(3.0) / 2.0])
        # Generate a modest grid around origin; L=4 yields sufficient
        pts = []
        L = 4
        for i in range(-L, L + 1):
            for j in range(-L, L + 1):
                p = i * b1 + j * b2
                if not include_origin and np.allclose(p, 0.0):
                    continue
                pts.append(p)
        pts = np.array(pts, dtype=np.float64)
        # Sort by radius
        radii2 = np.einsum("ij,ij->i", pts, pts)
        order = np.argsort(radii2)
        chosen = pts[order][:16]
        return chosen

    def rings_seed():
        """Concentric rings: 1 center + 6 on ring r1 + 9 on ring r2, spaced equally."""
        pts = []
        # Center
        pts.append([0.0, 0.0])
        # Inner ring with chord ~ 1
        m1 = 6
        r1 = 1.0  # chord length 2*r*sin(pi/m1) = 1 when r=1 for m1=6
        for k in range(m1):
            theta = 2.0 * math.pi * k / m1
            pts.append([r1 * math.cos(theta), r1 * math.sin(theta)])
        # Outer ring with chord near 1
        m2 = 9
        r2 = 1.45  # 2*r2*sin(pi/9) ~ 1
        for k in range(m2):
            theta = 2.0 * math.pi * k / m2
            pts.append([r2 * math.cos(theta), r2 * math.sin(theta)])
        return np.array(pts[:16], dtype=np.float64)

    def spiral_seed():
        """Golden-angle spiral in plane with gentle radial growth."""
        phi = math.pi * (3.0 - math.sqrt(5.0))  # golden angle ~ 2.399...
        pts = []
        a = 0.55
        for k in range(16):
            r = a * math.sqrt(k + 1)
            theta = k * phi
            pts.append([r * math.cos(theta), r * math.sin(theta)])
        return np.array(pts, dtype=np.float64)

    def poisson_disk_seed(R=2.4, max_trials=5000):
        """Greedy Poisson-disk sampling inside a disk of radius R with min distance ~1."""
        pts = []
        attempts = 0
        while len(pts) < 16 and attempts < max_trials:
            attempts += 1
            # Sample uniformly in disk
            u = rng.uniform(0.0, 1.0)
            r = R * math.sqrt(u)
            theta = rng.uniform(0.0, 2.0 * math.pi)
            p = np.array([r * math.cos(theta), r * math.sin(theta)], dtype=np.float64)
            # Check min distance constraint >= 1 among already placed points
            ok = True
            for q in pts:
                if np.linalg.norm(p - q) < 1.0:
                    ok = False
                    break
            if ok:
                pts.append(p)
        if len(pts) < 16:
            # Fallback to larger disk
            return poisson_disk_seed(R=3.0, max_trials=max_trials)
        return np.array(pts[:16], dtype=np.float64)

    # Build initial population with diverse seeds and jitter variants
    population = []

    # Hex-lattice closest 16 (with and without origin)
    population.append(hex_lattice_seed(include_origin=True))
    population.append(hex_lattice_seed(include_origin=False))

    # Rings and spiral
    population.append(rings_seed())
    population.append(spiral_seed())

    # Poisson-disk random seeds
    for _ in range(4):
        population.append(poisson_disk_seed(R=2.35))

    # Jittered hex variants
    for _ in range(4):
        base = hex_lattice_seed(include_origin=True)
        noise = rng.normal(scale=0.05, size=base.shape)
        population.append(base + noise)

    # Evolutionary outer loop
    pop_size = min(16, len(population))
    population = population[:pop_size]
    best_overall_P = None
    best_overall_ratio2 = float("inf")

    generations = 6
    for gen in range(generations):
        # Refine each candidate a moderate number of steps
        refined = []
        for idx in range(len(population)):
            P0 = population[idx]
            # Slight pre-jitter per generation to diversify
            P0 = P0 + rng.normal(scale=0.02, size=P0.shape)
            P_ref, r2 = refine_candidate(P0, steps=180, beta_start=12.0 + 2.0 * gen, beta_max=160.0)
            refined.append((P_ref, r2))

            if r2 < best_overall_ratio2:
                best_overall_ratio2 = r2
                best_overall_P = P_ref.copy()

        # Select elites
        refined.sort(key=lambda x: x[1])
        elites = refined[:max(4, pop_size // 3)]
        # Prepare next generation
        next_population = [e[0] for e in elites]

        # Jittered clones of elites
        while len(next_population) < pop_size - 2:
            parent_P = elites[rng.integers(0, len(elites))][0]
            sigma = 0.03 / (1.0 + 0.2 * gen)
            child = parent_P + rng.normal(scale=sigma, size=parent_P.shape)
            # Re-project and normalize to ensure feasibility
            child = project_min_distance(child, 1.0, max_passes=5)
            child = normalize_min_distance(child, 1.0)
            next_population.append(child)

        # Inject fresh random seeds to maintain diversity
        next_population.append(spiral_seed() + rng.normal(scale=0.03, size=(16, 2)))
        next_population.append(poisson_disk_seed(R=2.4))
        population = next_population

    # Final refinement on the best to polish
    best_overall_P, best_overall_ratio2 = refine_candidate(best_overall_P, steps=220, beta_start=20.0, beta_max=200.0)

    return best_overall_P.astype(np.float64), float(best_overall_ratio2)
# EVOLVE_END


if __name__ == "__main__":
    points, _ = optimize_construct(16, 2)
    print(json.dumps({"points": points.tolist()}))
```

```python
#!/usr/bin/env python3
"""Hex-sector + projected descent construction for 16 planar points minimizing diameter under unit minimum spacing."""

import json
import math
import numpy as np


# EVOLVE_START
def optimize_construct(n=16, d=2):
    """
    Construct n=16 points in 2D that (approximately) minimize R^2 = (D_max / D_min)^2.

    Strategy:
      - Normalize to D_min = 1; then minimize D_max^2.
      - Discrete stage: seed from compact sectors of a triangular (hexagonal) lattice (unit spacing).
      - Local discrete improvement: swap boundary points with inward lattice points to prune extremes.
      - Continuous polishing: projected gradient descent on a softmax surrogate of the diameter, with hard projection to enforce min distance >= 1 and global rescaling to pin D_min = 1.

    Returns:
      points: numpy array (16,2) of final positions
      ratio_squared: float representing (D_max / D_min)^2; with D_min normalized to 1, equals D_max^2.
    """
    if (n, d) != (16, 2):
        raise ValueError("the benchmark evaluates n=16 and d=2")

    # -----------------------------
    # Helper functions
    # -----------------------------

    def pairwise_distances(points):
        """Compute pairwise Euclidean distances matrix."""
        diff = points[:, None, :] - points[None, :, :]
        return np.linalg.norm(diff, axis=-1)

    def min_max_dist(points):
        """Compute (D_min, D_max) excluding zero diagonal distances."""
        D = pairwise_distances(points)
        # Mask diagonal
        nonzero = D[D > 0]
        return (float(np.min(nonzero)), float(np.max(nonzero)))

    def normalize_to_min1(points):
        """Rescale points so that the minimum nonzero distance equals 1."""
        Dmin, _ = min_max_dist(points)
        if Dmin <= 0:
            # Numerical emergency: add tiny jitter
            points = points + 1e-6 * np.random.randn(*points.shape)
            Dmin, _ = min_max_dist(points)
        scale = 1.0 / Dmin
        return points * scale

    def build_triangular_lattice(M=6):
        """
        Build a pool of triangular (hexagonal) lattice points with nearest neighbor spacing exactly 1.
        Basis: e1 = (1, 0), e2 = (0.5, sqrt(3)/2).
        Returns:
          pool_points: (K,2) array of points
          ij: (K,2) integer axial coordinates (i,j)
        """
        e1 = np.array([1.0, 0.0])
        e2 = np.array([0.5, math.sqrt(3) / 2.0])
        ij = []
        coords = []
        for i in range(-M, M + 1):
            for j in range(-M, M + 1):
                ij.append((i, j))
                coords.append(i * e1 + j * e2)
        return np.array(coords, dtype=float), np.array(ij, dtype=int)

    def angles_and_radii(points):
        """Compute polar angles in [0, 2π) and radii for each point."""
        angles = np.arctan2(points[:, 1], points[:, 0])
        # Convert to [0, 2π)
        angles = np.mod(angles, 2.0 * math.pi)
        radii = np.linalg.norm(points, axis=1)
        return angles, radii

    def angle_in_sector(angle, start, width):
        """Check if angle lies within [start, start+width) modulo 2π."""
        # Normalize
        two_pi = 2.0 * math.pi
        a = angle
        s = np.mod(start, two_pi)
        e = s + width
        if e <= two_pi:
            return (a >= s) & (a < e)
        else:
            # Wrap-around
            return (a >= s) | (a < (e - two_pi))

    def select_sector_points(pool_points, angles, radii, start_angle, width_rad, n_select):
        """
        Select up to n_select points closest to origin whose angles lie within the given sector.
        Returns (indices, selected_points). If not enough points, returns (None, None).
        """
        mask = angle_in_sector(angles, start_angle, width_rad)
        idx = np.where(mask)[0]
        if idx.size < n_select:
            return None, None
        # Sort by radius ascending and take first n_select
        idx_sorted = idx[np.argsort(radii[idx])]
        idx_sel = idx_sorted[:n_select]
        return idx_sel, pool_points[idx_sel]

    def compute_diameter(points):
        """Compute exact D_max (diameter) among points."""
        _, Dmax = min_max_dist(points)
        return Dmax

    def discrete_swap_improve(selected_idx, pool_points, angles, radii, max_iter=60, dtheta=0.35):
        """
        Greedy improvement: swap boundary points with inward candidates within angular neighborhood.
        selected_idx: indices into pool_points of current selection
        Returns improved indices (possibly unchanged).
        """
        selected_set = set(selected_idx.tolist())
        selected_pts = pool_points[selected_idx]
        best_diam = compute_diameter(selected_pts)
        # Precompute adjacency for speed
        for it in range(max_iter):
            pts = pool_points[np.array(list(selected_set))]
            D = pairwise_distances(pts)
            # Identify boundary points participating in max pairs
            Dmax = np.max(D[np.triu_indices(len(pts), k=1)])
            # Consider pairs within a small tolerance of Dmax
            tol = 1e-9
            boundary_pairs = np.argwhere(np.triu(D, k=1) >= Dmax - tol)
            boundary_indices = set(boundary_pairs.flatten().tolist())
            # Map back to pool indices
            sel_list = np.array(list(selected_set))
            boundary_pool_idx = sel_list[list(boundary_indices)]
            improved = False
            # Try swapping each boundary point with a candidate of smaller radius near angle
            for b_pool_idx in boundary_pool_idx:
                b_angle = angles[b_pool_idx]
                b_radius = radii[b_pool_idx]
                # Candidate set: unselected points with angle near b_angle and smaller radius
                candidates = np.where(
                    (~np.isin(np.arange(len(pool_points)), list(selected_set)))
                    & (np.abs(np.mod(angles - b_angle + math.pi, 2 * math.pi) - math.pi) <= dtheta)
                    & (radii <= b_radius + 1e-9)
                )[0]
                if candidates.size == 0:
                    continue
                # Sort candidates by increasing radius
                cand_sorted = candidates[np.argsort(radii[candidates])]
                # Try a few top candidates
                for c_idx in cand_sorted[:8]:
                    new_sel = selected_set.copy()
                    new_sel.remove(int(b_pool_idx))
                    new_sel.add(int(c_idx))
                    new_pts = pool_points[np.array(list(new_sel))]
                    new_diam = compute_diameter(new_pts)
                    if new_diam < best_diam - 1e-8:
                        selected_set = new_sel
                        best_diam = new_diam
                        improved = True
                        break
                if improved:
                    break
            if not improved:
                break
        return np.array(list(selected_set))

    def softmax_surrogate(D2, tau):
        """
        Smooth surrogate for max squared distance using softmax:
          f = (1/tau) * log(sum_{i<j} exp(tau * d_ij^2))
        """
        n = D2.shape[0]
        idx = np.triu_indices(n, k=1)
        # Log-sum-exp for stability
        z = tau * D2[idx]
        z_max = np.max(z)
        s = np.sum(np.exp(z - z_max))
        return (z_max + math.log(s)) / tau

    def surrogate_gradient(P, tau):
        """
        Gradient of the softmax surrogate w.r.t. points P.
        f = (1/tau) log sum_{i<j} exp(tau*||pi-pj||^2)
        grad_i = (2/s) * sum_{j!=i} exp(tau*||pi-pj||^2) * (pi - pj), where s = sum_{i<j} exp(tau*||pi-pj||^2)
        """
        n = P.shape[0]
        D2 = np.sum((P[:, None, :] - P[None, :, :]) ** 2, axis=-1)
        idx_u = np.triu_indices(n, k=1)
        Z_u = np.exp(tau * D2[idx_u])
        s = np.sum(Z_u)
        if not np.isfinite(s) or s <= 0.0:
            # Fallback small random gradient
            return np.random.randn(*P.shape) * 1e-3
        # Build symmetric weight matrix W with W[i,j] = exp(tau*dij^2) for i!=j
        W = np.zeros((n, n), dtype=float)
        W[idx_u] = Z_u
        W = W + W.T  # mirror to lower triangle
        # Gradient: (2/s)*(diag(sum_j W[i,j]) * P - W @ P)
        row_sums = np.sum(W, axis=1)  # (n,)
        G = (2.0 / s) * (row_sums[:, None] * P - W @ P)
        return G

    def project_min_distance(P, min_d=1.0, max_iters=8):
        """
        Enforce pairwise distances >= min_d by pairwise clamping along connecting segments.
        Then globally rescale so that min distance equals min_d.
        """
        X = P.copy()
        for _ in range(max_iters):
            D = pairwise_distances(X)
            # Find violations
            viol = np.argwhere((D > 0) & (D < min_d))
            if viol.size == 0:
                break
            # Process each violating pair (sequentially)
            for i, j in viol:
                if i >= j:
                    continue
                v = X[i] - X[j]
                dist = np.linalg.norm(v)
                if dist <= 1e-12:
                    # Random nudge to separate
                    v = np.random.randn(*v.shape)
                    dist = np.linalg.norm(v)
                # Move equally along line to set distance to min_d
                delta = (min_d - dist) / 2.0
                u = v / dist
                X[i] = X[i] + delta * u
                X[j] = X[j] - delta * u
        # Final rescale to pin min distance exactly to min_d
        Dmin, _ = min_max_dist(X)
        # Avoid division by zero
        if Dmin <= 0:
            X += 1e-6 * np.random.randn(*X.shape)
            Dmin, _ = min_max_dist(X)
        X = X * (min_d / Dmin)
        return X

    def continuous_polish(P_init, max_outer=3):
        """
        Projected gradient descent with softmax diameter surrogate.
        Schedules tau increasing; maintain D_min = 1 by projection and global rescale.
        Returns best points (by true D_max^2) and best ratio_squared.
        """
        # Normalize and center
        P = normalize_to_min1(P_init.copy())
        P = P - np.mean(P, axis=0, keepdims=True)

        tau_schedule = [3.0, 8.0, 16.0, 24.0]
        lr_base = 0.15
        best_P = P.copy()
        Dmin, Dmax = min_max_dist(P)
        best_ratio2 = (Dmax / Dmin) ** 2

        for outer in range(min(max_outer, len(tau_schedule))):
            tau = tau_schedule[outer]
            lr = lr_base / (1.0 + 0.25 * outer)
            # Run a fixed number of iterations for each tau
            patience = 20
            no_improve = 0
            for it in range(120):
                # Compute gradient and take a step with backtracking
                G = surrogate_gradient(P, tau)
                step_lr = lr
                accepted = False
                f_cur = softmax_surrogate(np.sum((P[:, None, :] - P[None, :, :]) ** 2, axis=-1), tau)
                for _ in range(10):
                    P_new = P - step_lr * G
                    # Center, project to maintain D_min >= 1, and re-center
                    P_new = P_new - np.mean(P_new, axis=0, keepdims=True)
                    P_new = project_min_distance(P_new, min_d=1.0, max_iters=4)
                    P_new = P_new - np.mean(P_new, axis=0, keepdims=True)
                    f_new = softmax_surrogate(np.sum((P_new[:, None, :] - P_new[None, :, :]) ** 2, axis=-1), tau)
                    if f_new <= f_cur + 1e-10:
                        accepted = True
                        P = P_new
                        break
                    step_lr *= 0.5
                if not accepted:
                    # Small random shake to escape; then continue
                    P = P + 0.01 * np.random.randn(*P.shape)
                    P = project_min_distance(P, min_d=1.0, max_iters=3)
                    P = P - np.mean(P, axis=0, keepdims=True)

                # Track best by true diameter
                Dmin, Dmax = min_max_dist(P)
                ratio2 = (Dmax / Dmin) ** 2
                if ratio2 < best_ratio2 - 1e-9:
                    best_ratio2 = ratio2
                    best_P = P.copy()
                    no_improve = 0
                else:
                    no_improve += 1

                # Occasional stochastic shake if stuck
                if no_improve >= patience:
                    P = P + 0.008 * np.random.randn(*P.shape)
                    P = project_min_distance(P, min_d=1.0, max_iters=3)
                    P = P - np.mean(P, axis=0, keepdims=True)
                    no_improve = 0

        return best_P, best_ratio2

    # -----------------------------
    # Main pipeline
    # -----------------------------

    # Build lattice pool
    pool_points, pool_ij = build_triangular_lattice(M=6)
    pool_angles, pool_radii = angles_and_radii(pool_points)

    # Sector widths (radians) and rotations
    widths_deg = [240, 270, 300]
    widths_rad = [w * math.pi / 180.0 for w in widths_deg]
    rotations = np.linspace(0.0, 2.0 * math.pi, 24, endpoint=False)

    # Discrete sector seeds
    seeds = []
    for w in widths_rad:
        for rot in rotations:
            idx_sel, pts_sel = select_sector_points(pool_points, pool_angles, pool_radii, rot, w, n_select=n)
            # If not enough points, widen slightly or skip
            if idx_sel is None:
                # Mildly widen and retry once
                idx_sel, pts_sel = select_sector_points(pool_points, pool_angles, pool_radii, rot, min(w + (15 * math.pi / 180.0), 2 * math.pi), n_select=n)
                if idx_sel is None:
                    continue
            # Center (translation does not affect distances but keeps things tidy)
            pts_sel_centered = pts_sel - np.mean(pts_sel, axis=0, keepdims=True)
            # Compute diameter with normalized min=1 to be fair
            pts_norm = normalize_to_min1(pts_sel_centered)
            diam = compute_diameter(pts_norm)
            seeds.append({
                "idx": idx_sel,
                "points": pts_norm,
                "diameter": diam,
                "width": w,
                "rotation": rot
            })

    if not seeds:
        # Fallback: simple grid (should not happen)
        points = np.array([[float(c), float(r)] for r in range(4) for c in range(4)], dtype=float)
        Dmin, Dmax = min_max_dist(points)
        return points, float((Dmax / Dmin) ** 2)

    # Keep a beam of the top seeds by diameter
    seeds_sorted = sorted(seeds, key=lambda s: s["diameter"])
    beam_k = min(12, len(seeds_sorted))
    beam = seeds_sorted[:beam_k]

    best_overall_points = None
    best_overall_ratio2 = float("inf")

    # Process each seed: discrete swap improvement, then continuous polishing
    for seed in beam:
        idx_sel = seed["idx"].copy()

        # Discrete swap improvement
        idx_improved = discrete_swap_improve(idx_sel, pool_points, pool_angles, pool_radii, max_iter=80, dtheta=0.35)
        pts_improved = pool_points[idx_improved]
        # Normalize and center
        pts_improved = normalize_to_min1(pts_improved)
        pts_improved = pts_improved - np.mean(pts_improved, axis=0, keepdims=True)

        # Continuous polish
        polished_pts, ratio2 = continuous_polish(pts_improved, max_outer=3)

        # Track best
        if ratio2 < best_overall_ratio2:
            best_overall_ratio2 = ratio2
            best_overall_points = polished_pts.copy()

    # Safety: ensure final min distance equals 1 exactly
    final_points = normalize_to_min1(best_overall_points)
    final_points = final_points - np.mean(final_points, axis=0, keepdims=True)
    Dmin, Dmax = min_max_dist(final_points)
    final_ratio2 = float((Dmax / Dmin) ** 2)

    return final_points, final_ratio2
# EVOLVE_END


if __name__ == "__main__":
    points, _ = optimize_construct(16, 2)
    print(json.dumps({"points": points.tolist()}))
```
