Insight from focusing the diameter surrogate on the top-K largest pairwise distances with an adaptive K(τ) during continuous polishing while enforcing D_min ≥ 1 by projection/rescaling.

- Top-K Softmax Diameter Surrogate and Focused Gradient: In the continuous polishing stage, it replaces the full-pair softmax over all pairwise squared distances with a softmax computed only over the K largest pairs and computes the gradient from that restricted set.
- Top-K Softmax Diameter Surrogate and Focused Gradient: It uses an adaptive K(τ) schedule that starts near 0.25·(n(n−1)/2) at low τ and increases toward 0.5·(n(n−1)/2) at higher τ; for n=16 (120 pairs), K ∈ {30, 45, 60}.
- Top-K Softmax Diameter Surrogate and Focused Gradient: By focusing descent on diameter-critical pairs while enforcing D_min ≥ 1 via projection and global rescaling, it sharpens the optimization signal and accelerates inward movement of extreme points.
- Top-K Softmax Diameter Surrogate and Focused Gradient: On the (n=16, d=2) task, it produced ratio_squared 12.927221888442855 with validity 1.0, evaluation time 23.729921674123034 seconds, and score 0.997063887603199.
- Beam Hex–Ring with Top-K Dual Surrogates and Hard Projection: Alternating two smooth surrogates with Top-K focus—(i) a soft ratio on log distances that protects Bottom-K minima and (ii) a Top-K softmax diameter surrogate—while enforcing a hard projection to keep D_min exactly 1 after every step and holding the selected pair sets stable during each line search, reliably contracts D_max without collapsing the minimum spacing; supported by the implementation’s Adam/backtracking loop with annealed temperatures and adaptive K, this configuration achieved a valid ratio_squared ≈ 12.9077 in ~60.66s, and should be reused when extreme pairs dominate the objective because it reduces oscillations and targets the true bottlenecks directly.

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
      - Continuous polishing: projected gradient descent on a top-K softmax surrogate of the diameter, with hard
        projection to enforce min distance >= 1 and global rescaling to pin D_min = 1.

    Mutation implemented:
      - Replace the full-pair softmax surrogate with a top-K softmax computed only over the K largest pairwise
        squared distances, and use the corresponding focused gradient. K adapts with the temperature τ:
          For n=16 (m=120 pairs): K ∈ {30, 45, 60} as τ increases.
      - This concentrates descent on diameter-critical pairs, accelerating inward movement of extremes while
        projection preserves D_min >= 1.
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

    def _topk_upper_pairs(D2, K):
        """
        Return indices (iu, ju) of the top-K largest entries of the upper triangular (k=1) of D2.
        If K is None or K >= number of pairs, returns all upper-triangular indices.
        """
        n = D2.shape[0]
        iu, ju = np.triu_indices(n, k=1)
        vals = D2[iu, ju]
        m = vals.size
        if (K is None) or (K >= m):
            return iu, ju
        # Use argpartition for efficiency; then sort descending among top-K for numerical stability in log-sum-exp
        top_idx_unsorted = np.argpartition(vals, m - K)[m - K:]
        # Optional: order by value descending
        order = np.argsort(vals[top_idx_unsorted])[::-1]
        top_idx = top_idx_unsorted[order]
        return iu[top_idx], ju[top_idx]

    def softmax_surrogate(D2, tau, iu_sel=None, ju_sel=None, K=None):
        """
        Smooth surrogate for max squared distance using softmax focused on top-K pairs:
          f = (1/tau) * log(sum_{(i,j) in S} exp(tau * d_ij^2)), where S are selected pairs.
        If iu_sel/ju_sel are provided, they define S. Otherwise, top-K from D2 is used if K is provided;
        else all upper-triangular pairs are used.
        """
        if (iu_sel is None) or (ju_sel is None):
            iu_sel, ju_sel = _topk_upper_pairs(D2, K)
        z = tau * D2[iu_sel, ju_sel]
        # Stability via log-sum-exp
        z_max = np.max(z)
        s = np.sum(np.exp(z - z_max))
        return (z_max + math.log(s)) / tau

    def surrogate_gradient(P, tau, iu_sel=None, ju_sel=None, K=None):
        """
        Gradient of the top-K softmax surrogate w.r.t. points P.
        f = (1/tau) log sum_{(i,j) in S} exp(tau*||pi-pj||^2), gradient uses only S.
        If iu_sel/ju_sel not provided, select top-K from current P; else use provided selection.
        """
        n = P.shape[0]
        # Compute squared distances
        diffs = P[:, None, :] - P[None, :, :]
        D2 = np.sum(diffs ** 2, axis=-1)

        if (iu_sel is None) or (ju_sel is None):
            iu_sel, ju_sel = _topk_upper_pairs(D2, K)

        z = np.exp(tau * D2[iu_sel, ju_sel])
        s = np.sum(z)
        if not np.isfinite(s) or s <= 0.0:
            # Fallback small random gradient
            return np.random.randn(*P.shape) * 1e-3

        # Build symmetric weight matrix W only for selected pairs
        W = np.zeros((n, n), dtype=float)
        W[iu_sel, ju_sel] = z
        W[ju_sel, iu_sel] = z
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
        Projected gradient descent with top-K softmax diameter surrogate.
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

        # Total number of unique pairs
        m_pairs = n * (n - 1) // 2

        def K_for_tau(tau):
            """Adaptive K schedule: low τ uses ~0.25 m, then ~0.375 m, then ~0.5 m."""
            if tau <= 5.0:
                return max(1, int(0.25 * m_pairs))  # ~30 for n=16
            elif tau <= 15.0:
                return max(1, int(0.375 * m_pairs))  # ~45 for n=16
            else:
                return max(1, int(0.5 * m_pairs))  # ~60 for n=16

        for outer in range(min(max_outer, len(tau_schedule))):
            tau = tau_schedule[outer]
            lr = lr_base / (1.0 + 0.25 * outer)
            K = min(K_for_tau(tau), m_pairs)

            # Run a fixed number of iterations for each tau
            patience = 20
            no_improve = 0
            for it in range(120):
                # Precompute current squared distances and select top-K pairs
                D2_cur = np.sum((P[:, None, :] - P[None, :, :]) ** 2, axis=-1)
                iu_sel, ju_sel = _topk_upper_pairs(D2_cur, K)

                # Compute gradient on selected pairs and take a step with backtracking
                G = surrogate_gradient(P, tau, iu_sel=iu_sel, ju_sel=ju_sel)
                step_lr = lr
                accepted = False
                f_cur = softmax_surrogate(D2_cur, tau, iu_sel=iu_sel, ju_sel=ju_sel)
                for _ in range(10):
                    P_new = P - step_lr * G
                    # Center, project to maintain D_min >= 1, and re-center
                    P_new = P_new - np.mean(P_new, axis=0, keepdims=True)
                    P_new = project_min_distance(P_new, min_d=1.0, max_iters=4)
                    P_new = P_new - np.mean(P_new, axis=0, keepdims=True)
                    D2_new = np.sum((P_new[:, None, :] - P_new[None, :, :]) ** 2, axis=-1)
                    # IMPORTANT: keep the same selected pairs during line-search for a coherent surrogate
                    f_new = softmax_surrogate(D2_new, tau, iu_sel=iu_sel, ju_sel=ju_sel)
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

```python
#!/usr/bin/env python3
"""Hex-sector + dual-surrogate projected descent construction for 16 planar points minimizing diameter under unit minimum spacing.

This implementation combines:
  - Multi-topology beam seeding: hex/triangular-lattice sector seeds and concentric-ring seeds.
  - Discrete boundary-pruning swaps for lattice seeds.
  - Dual smooth surrogates in continuous refinement:
      a) Top-K softmax on squared distances (diameter surrogate).
      b) Soft ratio on log distances with Top-K focus for max and Bottom-K focus for min.
  - Stable Top-K pair selection during line-search and adaptive K schedule.
  - Hard projection to enforce D_min >= 1 and final rescale to D_min == 1, always.
  - Occasional extremal-pair equalization micro-steps.

With D_min pinned to 1, minimizing R^2 = (D_max / D_min)^2 equals minimizing D_max^2.

The routine optimize_construct(n=16, d=2) returns:
  - points: (16,2) NumPy array of point coordinates.
  - ratio_squared: exact (D_max / D_min) ^ 2 value of the configuration.
"""

import json
import math
import numpy as np


# EVOLVE_START
def optimize_construct(n=16, d=2):
    """
    Construct n=16 points in 2D that (approximately) minimize R^2 = (D_max / D_min)^2.

    Strategy (hybrid from the provided description):
      - Normalize to D_min = 1; then minimize D_max^2.
      - Multi-topology seeding:
          * Compact sectors of a triangular (hexagonal) lattice (unit spacing).
          * Concentric ring configurations with counts summing to 16 and tuned radii/phases.
      - Local discrete improvement (for lattice seeds): swap boundary points with inward lattice points to prune extremes.
      - Dual-surrogate continuous refinement alternating blocks:
          * Top-K softmax surrogate of the diameter (squared distances).
          * Soft ratio on log distances with Top-K (max) and Bottom-K (min) focusing.
        Optimization uses backtracking with stable Top-K pair sets. After each step: recenter, project to enforce D_min>=1,
        rescale so D_min=1, and add tiny decaying noise.
      - Targeted extremal-pair equalization micro-steps interleaved.
      - Beam search across diverse seeds, keeping best by exact D_max^2.
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
        for _ in range(max_iter):
            sel_list = np.array(list(selected_set))
            pts = pool_points[sel_list]
            D = pairwise_distances(pts)
            # Identify boundary points participating in max pairs
            Dmax = np.max(D[np.triu_indices(len(pts), k=1)])
            tol = 1e-9
            boundary_pairs = np.argwhere(np.triu(D, k=1) >= Dmax - tol)
            boundary_indices = set(boundary_pairs.flatten().tolist())
            boundary_pool_idx = sel_list[list(boundary_indices)]
            improved = False
            # Try swapping each boundary point with a candidate of smaller radius near angle
            for b_pool_idx in boundary_pool_idx:
                b_angle = angles[b_pool_idx]
                b_radius = radii[b_pool_idx]
                candidates = np.where(
                    (~np.isin(np.arange(len(pool_points)), list(selected_set)))
                    & (np.abs(np.mod(angles - b_angle + math.pi, 2 * math.pi) - math.pi) <= dtheta)
                    & (radii <= b_radius + 1e-9)
                )[0]
                if candidates.size == 0:
                    continue
                cand_sorted = candidates[np.argsort(radii[candidates])]
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

    def _topk_upper_pairs(D2, K):
        """
        Return indices (iu, ju) of the top-K largest entries of the upper triangular (k=1) of D2.
        If K is None or K >= number of pairs, returns all upper-triangular indices.
        """
        nloc = D2.shape[0]
        iu, ju = np.triu_indices(nloc, k=1)
        vals = D2[iu, ju]
        m = vals.size
        if (K is None) or (K >= m):
            return iu, ju
        top_idx_unsorted = np.argpartition(vals, m - K)[m - K:]
        order = np.argsort(vals[top_idx_unsorted])[::-1]
        top_idx = top_idx_unsorted[order]
        return iu[top_idx], ju[top_idx]

    def _bottomk_upper_pairs(values, K):
        """
        Return indices (iu, ju) of the bottom-K smallest entries of the upper triangular (k=1) of
        a symmetric matrix given by values (same shape as D or logD). 'values' should be (n,n) with diag ignored.
        """
        nloc = values.shape[0]
        iu, ju = np.triu_indices(nloc, k=1)
        vals = values[iu, ju]
        m = vals.size
        if (K is None) or (K >= m):
            return iu, ju
        bot_idx_unsorted = np.argpartition(vals, K - 1)[:K]
        order = np.argsort(vals[bot_idx_unsorted])  # ascending
        bot_idx = bot_idx_unsorted[order]
        return iu[bot_idx], ju[bot_idx]

    def softmax_surrogate(D2, tau, iu_sel=None, ju_sel=None, K=None):
        """
        Smooth surrogate for max squared distance using softmax focused on top-K pairs:
          f = (1/tau) * log(sum_{(i,j) in S} exp(tau * d_ij^2)), where S are selected pairs.
        If iu_sel/ju_sel are provided, they define S. Otherwise, top-K from D2 is used if K is provided;
        else all upper-triangular pairs are used.
        """
        if (iu_sel is None) or (ju_sel is None):
            iu_sel, ju_sel = _topk_upper_pairs(D2, K)
        z = tau * D2[iu_sel, ju_sel]
        z_max = np.max(z)
        s = np.sum(np.exp(z - z_max))
        return (z_max + math.log(s)) / tau

    def surrogate_gradient(P, tau, iu_sel=None, ju_sel=None, K=None):
        """
        Gradient of the top-K softmax surrogate w.r.t. points P.
        f = (1/tau) log sum_{(i,j) in S} exp(tau*||pi-pj||^2), gradient uses only S.
        If iu_sel/ju_sel not provided, select top-K from current P; else use provided selection.
        """
        nloc = P.shape[0]
        diffs = P[:, None, :] - P[None, :, :]
        D2 = np.sum(diffs ** 2, axis=-1)

        if (iu_sel is None) or (ju_sel is None):
            iu_sel, ju_sel = _topk_upper_pairs(D2, K)

        z = np.exp(tau * D2[iu_sel, ju_sel])
        s = np.sum(z)
        if not np.isfinite(s) or s <= 0.0:
            return np.random.randn(*P.shape) * 1e-3

        # Build symmetric weight matrix W only for selected pairs
        W = np.zeros((nloc, nloc), dtype=float)
        W[iu_sel, ju_sel] = z
        W[ju_sel, iu_sel] = z
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
            for i, j in viol:
                if i >= j:
                    continue
                v = X[i] - X[j]
                dist = np.linalg.norm(v)
                if dist <= 1e-12:
                    v = np.random.randn(*v.shape)
                    dist = np.linalg.norm(v)
                delta = (min_d - dist) / 2.0
                u = v / dist
                X[i] = X[i] + delta * u
                X[j] = X[j] - delta * u
        Dmin, _ = min_max_dist(X)
        if Dmin <= 0:
            X += 1e-6 * np.random.randn(*X.shape)
            Dmin, _ = min_max_dist(X)
        X = X * (min_d / Dmin)
        return X

    # --------- Soft ratio on log distances (dual surrogate) ---------

    def ratio_surrogate_value(P, tau_r, iu_max=None, ju_max=None, iu_min=None, ju_min=None,
                              K_top=None, K_bottom=None):
        """
        f_ratio = smax_log - smin_log, where smax_log = (1/tau_r) log sum exp(tau_r * logd_ij)
                                          and smin_log = -(1/tau_r) log sum exp(-tau_r * logd_ij)
        Focuses on Top-K for smax and Bottom-K for smin if pair sets are provided or K specified.
        """
        D = pairwise_distances(P)
        nloc = P.shape[0]
        # Avoid zeros on diagonal; mask not needed as we index upper-triangular.
        logD = np.log(np.maximum(D, 1e-12))

        # Select pairs
        if (iu_max is None) or (ju_max is None):
            # Top-K largest log distances
            iu_max, ju_max = _topk_upper_pairs(logD, K_top)
        if (iu_min is None) or (ju_min is None):
            # Bottom-K smallest log distances
            iu_min, ju_min = _bottomk_upper_pairs(logD, K_bottom)

        zmax = tau_r * logD[iu_max, ju_max]
        zmax_max = np.max(zmax)
        smax = (zmax_max + math.log(np.sum(np.exp(zmax - zmax_max)))) / tau_r

        zmin = -tau_r * logD[iu_min, ju_min]
        zmin_max = np.max(zmin)
        smin = - (zmin_max + math.log(np.sum(np.exp(zmin - zmin_max)))) / tau_r

        return smax - smin

    def ratio_surrogate_gradient(P, tau_r, iu_max=None, ju_max=None, iu_min=None, ju_min=None,
                                 K_top=None, K_bottom=None):
        """
        Gradient of f_ratio = smax_log - smin_log with focused Top-K (max) and Bottom-K (min) pair sets.
        grad(log d_ij) w.r.t. positions:
            ∂/∂x_i log d_ij = (x_i - x_j) / d_ij^2
            ∂/∂x_j log d_ij = - (x_i - x_j) / d_ij^2
        """
        nloc = P.shape[0]
        D = pairwise_distances(P)
        logD = np.log(np.maximum(D, 1e-12))
        # Avoid any division by zero
        D2_safe = np.maximum(D ** 2, 1e-12)

        # Pair sets
        if (iu_max is None) or (ju_max is None):
            iu_max, ju_max = _topk_upper_pairs(logD, K_top)
        if (iu_min is None) or (ju_min is None):
            iu_min, ju_min = _bottomk_upper_pairs(logD, K_bottom)

        # Weights for smax (softmax over tau_r * logd on selected top pairs)
        zmax = tau_r * logD[iu_max, ju_max]
        zmax_max = np.max(zmax)
        wmax = np.exp(zmax - zmax_max)
        wmax_sum = np.sum(wmax)
        if not np.isfinite(wmax_sum) or wmax_sum <= 0.0:
            wmax = np.ones_like(zmax)
            wmax_sum = np.sum(wmax)
        wmax = wmax / wmax_sum

        # Weights for smin (softmin over logd on selected bottom pairs) => softmax over -tau_r * logd
        zmin = -tau_r * logD[iu_min, ju_min]
        zmin_max = np.max(zmin)
        wmin = np.exp(zmin - zmin_max)
        wmin_sum = np.sum(wmin)
        if not np.isfinite(wmin_sum) or wmin_sum <= 0.0:
            wmin = np.ones_like(zmin)
            wmin_sum = np.sum(wmin)
        wmin = wmin / wmin_sum

        # Accumulate gradient
        G = np.zeros_like(P)

        # Contribute from smax
        for idx, (i, j) in enumerate(zip(iu_max, ju_max)):
            # grad log d_ij
            v = P[i] - P[j]
            g = v / D2_safe[i, j]
            w = wmax[idx]
            G[i] += w * g
            G[j] -= w * g

        # Subtract contribution from smin
        for idx, (i, j) in enumerate(zip(iu_min, ju_min)):
            v = P[i] - P[j]
            g = v / D2_safe[i, j]
            w = wmin[idx]
            G[i] -= w * g
            G[j] += w * g

        return G

    # ------------- Concentric ring seeds -------------

    def base_ring_radius(m):
        """
        For m uniformly placed points on a circle, nearest-neighbor chord distance is 2 R sin(pi/m).
        Set this ~ 1 => R ≈ 1 / (2 sin(pi/m)).
        """
        return 1.0 / (2.0 * math.sin(math.pi / max(m, 3)))

    def make_ring_points(counts, scales=None, phases=None):
        """
        Build concentric ring configuration.
        counts: list of integers summing to n (e.g., [6,10]).
        scales: optional list of multiplicative scalars for each ring's base radius (default 1).
        phases: optional list of phase offsets (radians) per ring (default random small offsets).
        Returns an (n,2) array of points.
        """
        assert sum(counts) == n
        R = []
        for m in counts:
            R.append(base_ring_radius(m))
        R = np.array(R, dtype=float)
        if scales is None:
            scales = np.ones_like(R)
        else:
            scales = np.array(scales, dtype=float)
            if scales.shape != R.shape:
                raise ValueError("scales must match counts length")
        if phases is None:
            phases = np.random.uniform(0.0, 2.0 * math.pi, size=len(counts))
        pts = []
        idx0 = 0
        for r_idx, m in enumerate(counts):
            rad = R[r_idx] * scales[r_idx]
            phi = phases[r_idx]
            for k in range(m):
                theta = phi + 2.0 * math.pi * (k / m)
                pts.append([rad * math.cos(theta), rad * math.sin(theta)])
            idx0 += m
        pts = np.array(pts, dtype=float)
        return pts

    def ring_seed_candidates():
        """
        Generate multiple ring-based seeds by sampling scales and phases around defaults.
        Returns a list of dicts: {"points": pts_norm, "diameter": diam, "meta": {...}}
        """
        ring_counts_list = [
            [6, 10],
            [7, 9],
            [2, 6, 8],
            [1, 5, 10],
            [8, 8]
        ]
        seeds = []
        rng = np.random.default_rng(1234)
        for counts in ring_counts_list:
            # Sample several scale/phase combinations
            trials = 18
            for _ in range(trials):
                # Scales around 1 with slight inward bias for inner rings
                scales = []
                for ridx, m in enumerate(counts):
                    bias = 1.0 - 0.07 * ridx  # inner slightly smaller
                    scales.append(bias * (1.0 + 0.15 * (rng.random() - 0.5)))
                phases = rng.uniform(0.0, 2.0 * math.pi, size=len(counts))
                pts = make_ring_points(counts, scales=scales, phases=phases)
                # Center and normalize
                pts = pts - np.mean(pts, axis=0, keepdims=True)
                pts = normalize_to_min1(pts)
                diam = compute_diameter(pts)
                seeds.append({
                    "points": pts,
                    "diameter": diam,
                    "counts": counts,
                    "type": "ring"
                })
        return seeds

    # ------------- Continuous polish (dual surrogate) -------------

    def extremal_equalization_step(P, step=0.05):
        """
        Micro-step along extremal pairs:
          - Push min pair apart slightly.
          - Pull max pair together slightly.
        Then project and recenter.
        """
        D = pairwise_distances(P)
        nloc = P.shape[0]
        iu, ju = np.triu_indices(nloc, k=1)
        vals = D[iu, ju]
        min_idx = iu[np.argmin(vals)], ju[np.argmin(vals)]
        max_idx = iu[np.argmax(vals)], ju[np.argmax(vals)]
        i_min, j_min = min_idx
        i_max, j_max = max_idx

        # Avoid zero distances
        # Push min pair apart
        vm = P[i_min] - P[j_min]
        dm = np.linalg.norm(vm)
        if dm < 1e-12:
            vm = np.random.randn(*vm.shape)
            dm = np.linalg.norm(vm)
        um = vm / dm
        P[i_min] = P[i_min] + 0.5 * step * um
        P[j_min] = P[j_min] - 0.5 * step * um

        # Pull max pair together
        vM = P[i_max] - P[j_max]
        dM = np.linalg.norm(vM)
        if dM < 1e-12:
            vM = np.random.randn(*vM.shape)
            dM = np.linalg.norm(vM)
        uM = vM / dM
        P[i_max] = P[i_max] - 0.5 * step * uM
        P[j_max] = P[j_max] + 0.5 * step * uM

        # Project and recenter
        P = project_min_distance(P, min_d=1.0, max_iters=3)
        P = P - np.mean(P, axis=0, keepdims=True)
        return P

    def continuous_polish(P_init, max_outer_blocks=6):
        """
        Projected gradient descent alternating surrogates:
          - Soft ratio on log distances with Top-K/Bottom-K focusing (annealed τ_r).
          - Top-K softmax on squared distances (diameter surrogate) with increasing τ_d.
        Maintain D_min = 1 by projection and global rescale; recenter after each step.
        Returns best points (by true D_max^2) and best ratio_squared.
        """
        P = normalize_to_min1(P_init.copy())
        P = P - np.mean(P, axis=0, keepdims=True)

        # Schedules
        tau_r_sched = [1.0, 0.6, 0.4]
        tau_d_sched = [8.0, 16.0, 24.0]
        # Build sequence alternating ratio and diameter
        stages = []
        for i in range(min(len(tau_r_sched), 3)):
            stages.append(("ratio", tau_r_sched[i], 90))
            stages.append(("diam", tau_d_sched[min(i, len(tau_d_sched) - 1)], 120))
        # Possibly add a final strong diameter polish
        stages.append(("diam", 24.0, 140))

        lr_ratio = 0.15
        lr_diam = 0.12

        # Track best exact ratio^2 (with D_min pinned, equals D_max^2)
        Dmin, Dmax = min_max_dist(P)
        best_P = P.copy()
        best_ratio2 = (Dmax / Dmin) ** 2

        m_pairs = n * (n - 1) // 2

        def K_for_tau_d(tau):
            if tau <= 5.0:
                return max(1, int(0.25 * m_pairs))  # ~30 for n=16
            elif tau <= 15.0:
                return max(1, int(0.375 * m_pairs))  # ~45
            else:
                return max(1, int(0.5 * m_pairs))  # ~60

        # Bottom-K for ratio's min term
        def K_bottom_ratio():
            return max(1, int(0.15 * m_pairs))

        rng = np.random.default_rng(123)

        patience = 25
        no_improve = 0

        block_count = 0
        for (stype, tau, steps) in stages[:max_outer_blocks]:
            block_count += 1
            for it in range(steps):
                if stype == "ratio":
                    # Build pair sets
                    D = pairwise_distances(P)
                    logD = np.log(np.maximum(D, 1e-12))
                    K_top = max(1, int(0.35 * m_pairs))
                    iu_max, ju_max = _topk_upper_pairs(logD, K_top)
                    iu_min, ju_min = _bottomk_upper_pairs(logD, K_bottom_ratio())
                    # Current value and gradient
                    f_cur = ratio_surrogate_value(P, tau, iu_max=iu_max, ju_max=ju_max, iu_min=iu_min, ju_min=ju_min)
                    G = ratio_surrogate_gradient(P, tau, iu_max=iu_max, ju_max=ju_max, iu_min=iu_min, ju_min=ju_min)
                    step_lr = lr_ratio
                else:
                    # Diameter
                    diffs = P[:, None, :] - P[None, :, :]
                    D2_cur = np.sum(diffs ** 2, axis=-1)
                    K = min(K_for_tau_d(tau), m_pairs)
                    iu_sel, ju_sel = _topk_upper_pairs(D2_cur, K)
                    f_cur = softmax_surrogate(D2_cur, tau, iu_sel=iu_sel, ju_sel=ju_sel)
                    G = surrogate_gradient(P, tau, iu_sel=iu_sel, ju_sel=ju_sel)
                    step_lr = lr_diam

                # Backtracking with stable pair sets
                accepted = False
                for _ in range(10):
                    P_new = P - step_lr * G
                    # Center and project
                    P_new = P_new - np.mean(P_new, axis=0, keepdims=True)
                    P_new = project_min_distance(P_new, min_d=1.0, max_iters=4)
                    P_new = P_new - np.mean(P_new, axis=0, keepdims=True)
                    # Evaluate with the SAME pair sets and surrogate
                    if stype == "ratio":
                        f_new = ratio_surrogate_value(P_new, tau, iu_max=iu_max, ju_max=ju_max, iu_min=iu_min, ju_min=ju_min)
                    else:
                        D2_new = np.sum((P_new[:, None, :] - P_new[None, :, :]) ** 2, axis=-1)
                        f_new = softmax_surrogate(D2_new, tau, iu_sel=iu_sel, ju_sel=ju_sel)
                    if f_new <= f_cur + 1e-10:
                        accepted = True
                        P = P_new
                        break
                    step_lr *= 0.5
                if not accepted:
                    # Small stochastic shake if rejected
                    P = P + 0.008 * rng.standard_normal(P.shape)
                    P = project_min_distance(P, min_d=1.0, max_iters=3)
                    P = P - np.mean(P, axis=0, keepdims=True)

                # Occasional extremal equalization micro-step
                if (it + 1) % 12 == 0:
                    P = extremal_equalization_step(P, step=0.04)

                # Tiny decaying noise to avoid shallow basins
                noise_scale = max(0.0, 0.02 * (1.0 - 0.75 * (block_count / max_outer_blocks)))
                if noise_scale > 0 and (it % 20 == 0):
                    P = P + noise_scale * 0.01 * rng.standard_normal(P.shape)
                    P = project_min_distance(P, min_d=1.0, max_iters=2)
                    P = P - np.mean(P, axis=0, keepdims=True)

                # Track best by true diameter (with D_min pinned)
                Dmin, Dmax = min_max_dist(P)
                ratio2 = (Dmax / Dmin) ** 2
                if ratio2 < best_ratio2 - 1e-9:
                    best_ratio2 = ratio2
                    best_P = P.copy()
                    no_improve = 0
                else:
                    no_improve += 1

                if no_improve >= patience:
                    # Shake
                    P = P + 0.01 * rng.standard_normal(P.shape)
                    P = project_min_distance(P, min_d=1.0, max_iters=3)
                    P = P - np.mean(P, axis=0, keepdims=True)
                    no_improve = 0

        # Final sharp polish focused on diameter at highest tau
        tau_final = 28.0
        for it in range(160):
            diffs = P[:, None, :] - P[None, :, :]
            D2_cur = np.sum(diffs ** 2, axis=-1)
            # Note: ensure K is an int value not a tuple
            K = max(1, int(0.55 * m_pairs))
            iu_sel, ju_sel = _topk_upper_pairs(D2_cur, K)
            f_cur = softmax_surrogate(D2_cur, tau_final, iu_sel=iu_sel, ju_sel=ju_sel)
            G = surrogate_gradient(P, tau_final, iu_sel=iu_sel, ju_sel=ju_sel)
            step_lr = 0.1
            accepted = False
            for _ in range(10):
                P_new = P - step_lr * G
                P_new = P_new - np.mean(P_new, axis=0, keepdims=True)
                P_new = project_min_distance(P_new, min_d=1.0, max_iters=4)
                P_new = P_new - np.mean(P_new, axis=0, keepdims=True)
                D2_new = np.sum((P_new[:, None, :] - P_new[None, :, :]) ** 2, axis=-1)
                f_new = softmax_surrogate(D2_new, tau_final, iu_sel=iu_sel, ju_sel=ju_sel)
                if f_new <= f_cur + 1e-10:
                    accepted = True
                    P = P_new
                    break
                step_lr *= 0.5
            if not accepted:
                P = P + 0.006 * rng.standard_normal(P.shape)
                P = project_min_distance(P, min_d=1.0, max_iters=3)
                P = P - np.mean(P, axis=0, keepdims=True)

            if (it + 1) % 20 == 0:
                P = extremal_equalization_step(P, step=0.03)

            Dmin, Dmax = min_max_dist(P)
            ratio2 = (Dmax / Dmin) ** 2
            if ratio2 < best_ratio2 - 1e-9:
                best_ratio2 = ratio2
                best_P = P.copy()

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
            if idx_sel is None:
                # Mildly widen and retry once
                idx_sel, pts_sel = select_sector_points(
                    pool_points, pool_angles, pool_radii, rot,
                    min(w + (15 * math.pi / 180.0), 2 * math.pi),
                    n_select=n
                )
                if idx_sel is None:
                    continue
            pts_sel_centered = pts_sel - np.mean(pts_sel, axis=0, keepdims=True)
            pts_norm = normalize_to_min1(pts_sel_centered)
            diam = compute_diameter(pts_norm)
            seeds.append({
                "idx": idx_sel,
                "points": pts_norm,
                "diameter": diam,
                "width": w,
                "rotation": rot,
                "type": "lattice"
            })

    # Add ring seeds
    ring_seeds = ring_seed_candidates()
    seeds.extend(ring_seeds)

    if not seeds:
        # Fallback: simple grid (should not happen)
        points = np.array([[float(c), float(r)] for r in range(4) for c in range(4)], dtype=float)
        Dmin, Dmax = min_max_dist(points)
        return points, float((Dmax / Dmin) ** 2)

    # Keep a beam of the top seeds by diameter (with D_min=1 normalization)
    seeds_sorted = sorted(seeds, key=lambda s: s.get("diameter", compute_diameter(s["points"])))
    beam_k = min(16, len(seeds_sorted))
    beam = seeds_sorted[:beam_k]

    best_overall_points = None
    best_overall_ratio2 = float("inf")

    # Process each seed: discrete swap improvement for lattice seeds, then continuous polishing
    for seed in beam:
        seed_pts = seed["points"]
        seed_type = seed.get("type", "lattice")
        pts_start = seed_pts

        if seed_type == "lattice":
            idx_sel = seed["idx"].copy()
            # Discrete swap improvement
            idx_improved = discrete_swap_improve(idx_sel, pool_points, pool_angles, pool_radii, max_iter=80, dtheta=0.35)
            pts_improved = pool_points[idx_improved]
            pts_improved = normalize_to_min1(pts_improved)
            pts_improved = pts_improved - np.mean(pts_improved, axis=0, keepdims=True)
            pts_start = pts_improved
        else:
            # Already normalized and centered in generation
            pts_start = seed_pts

        # Continuous polish (dual-surrogate)
        polished_pts, ratio2 = continuous_polish(pts_start, max_outer_blocks=7)

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
