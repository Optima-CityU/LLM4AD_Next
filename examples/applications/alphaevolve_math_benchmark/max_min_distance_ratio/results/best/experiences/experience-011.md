Insight on why the caliper‑gated, scale‑invariant contraction with NBC‑POCS and disjoint matching performed well in this run (score 0.9996769864943461; ratio_squared 12.893430864303385; validity 1.0).

- Monotone NBC‑PMD‑ADM: Gating every move on the exact D_max^2 after NBC‑POCS repair and D_min = 1 normalization (strict non‑increase with tol ≈ 1e−9 and backtracking) turned the smooth‑max descent and the disjoint near/far matching into a stable, monotone D_max^2 minimizer; future designs should compute acceptance on the true D_max post‑repair/normalization and reject or backtrack steps that do not strictly reduce it. In this setup, the NBC‑POCS projector splits corrections using near‑band congestion weights w_k = 1/(1 + near‑band count) with under‑relaxation ρ ≈ 0.9 to curb diameter inflation, while annealed schedules (β ≈ 12→160–200, K = 3→5, γ_far ≈ 0.03→0.08, γ_near ≈ 0.03→0.015, momentum μ ≈ 0.8–0.9) efficiently shrink multiple diameter directions. This combination achieved ratio_squared 12.893430864303385 with validity 1.0 in the recorded run, indicating the gate‑then‑contract pattern is effective for n = 16, d = 2.

```python
#!/usr/bin/env python3
"""16-point planar construction minimizing max/min distance ratio via hybrid NBC-POCS + Smooth‑Max + Disjoint Matching.

This implementation targets minimizing R^2 = (D_max / D_min)^2 for n=16, d=2.

Key features:
- Scale-invariant normalization: every iterate is re-centered and scaled so D_min = 1,
  reducing the objective to R^2 = D_max^2.
- Smooth-max (log-sum-exp) descent on squared pairwise distances with a strict monotone gate:
  accept a step only if the exact D_max^2 does not increase; otherwise backtrack and retry.
- NBC-POCS projector enforcing the hard min-distance constraint by pushing violating pairs apart,
  allocating separation to less-crowded endpoints and under-relaxing updates to curb diameter inflation.
- Alternating globally disjoint matching step targeting extreme (near/far) pairs with
  directed repulsive/contractive moves, softmin/softmax weights, annealed strengths, and a
  convex-hull-caliper preference for far pairs to directly shrink true diameter directions.
- Diverse seeding: hex-lattice shells (with rotations), rings, Poisson-disk, spiral, plus jittered variants.
- Evolutionary outer loop maintaining exploration while polishing elites under the same monotone gate.

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
        """NBC-POCS projector: enforce ||pi - pj|| >= min_d by pushing violating pairs apart.

        Congestion-aware split:
        - Compute near-band neighbor counts cong[k] = 1 + |{j : ||pk − pj|| ≤ r_band}|,
          where r_band ≈ 1.05 * min_d (diagonal excluded).
        - Use weights w_k = 1/(cong[k] + eps) to split the per-pair correction so that
          less-crowded endpoints move more.
        - Under-relax each per-pair correction by factor rho ≈ 0.9 to stabilize updates.

        Recenter after each pass. This helps curb hull drift and diameter inflation.
        """
        n = len(P)
        eps = 1e-9
        rho = 0.9
        r_band = 1.05 * (min_d if min_d > 0 else 1.0)
        r_band2 = r_band * r_band

        # Iterate a few passes to resolve violations
        for _ in range(max_passes):
            D2 = pairwise_sqdist(P)

            # Identify violating pairs
            viol_mask = (~np.eye(n, dtype=bool)) & (D2 < (min_d - tol) ** 2)
            if not np.any(viol_mask):
                break

            # Near-band congestion mask including near-contacts slightly above threshold
            near_band = (~np.eye(n, dtype=bool)) & (D2 <= r_band2)
            # Congestion score per point: 1 + number of near-band neighbors
            cong = 1 + near_band.sum(axis=1)
            # Split weights favoring less-crowded points
            w = 1.0 / (cong.astype(np.float64) + eps)

            corr = np.zeros_like(P)
            # Process only upper triangle to avoid double counting
            rows, cols = np.where(np.triu(viol_mask, k=1))
            for i, j in zip(rows, cols):
                dij2 = D2[i, j]
                dij = math.sqrt(max(dij2, 0.0))
                # Needed total increase in separation to reach min_d
                need_total = max(min_d - dij, 0.0)
                if need_total <= 0:
                    continue

                # Unit direction from j to i
                if dij > 1e-12:
                    u = (P[i] - P[j]) / dij
                else:
                    # If coincident or extremely close, pick a random unit direction
                    theta = rng.uniform(0.0, 2.0 * math.pi)
                    u = np.array([math.cos(theta), math.sin(theta)], dtype=np.float64)

                # Split correction based on local congestion weights
                wi = w[i]
                wj = w[j]
                s = wi + wj
                if s <= eps:
                    ai = aj = 0.5
                else:
                    ai = wi / s
                    aj = wj / s

                # Under-relaxed per-pair correction
                delta_i = rho * ai * need_total * u
                delta_j = -rho * aj * need_total * u

                corr[i] += delta_i
                corr[j] += delta_j

            # Apply cumulative correction for this pass and recenter
            P = P + corr
            P = P - P.mean(axis=0, keepdims=True)

        return P

    def smooth_max_gradient(P, beta):
        """Compute gradient of f = (1/beta) * log sum_{i<j} exp(beta * ||pi-pj||^2).

        We work with a symmetric weight matrix W over all ordered pairs (i,j), i!=j.
        Z = 0.5 * sum W approximates the unordered-pair partition function.
        Gradient: g_i = (2/Z) * sum_j W_ij * (pi - pj).
        """
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
        # so take Z as half the total sum to represent unordered pairs.
        Z = 0.5 * W.sum()
        if Z <= 1e-16:
            # Degenerate case: return zero gradient
            return np.zeros_like(P)
        # Gradient: g_i = (2/Z) * sum_j W_ij * (pi - pj)
        row_sums = W.sum(axis=1)  # (n,)
        g = (2.0 / Z) * (P * row_sums[:, None] - W @ P)
        return g

    def convex_hull_indices(P):
        """Compute convex hull indices (monotone chain). Returns list of point indices on hull in CCW order."""
        pts = [(float(P[i, 0]), float(P[i, 1]), i) for i in range(len(P))]
        # Sort by x, then y
        pts.sort()
        def cross(o, a, b):
            return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

        # Build lower hull
        lower = []
        for p in pts:
            while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
                lower.pop()
            lower.append(p)
        # Build upper hull
        upper = []
        for p in reversed(pts):
            while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
                upper.pop()
            upper.append(p)
        # Concatenate, removing last element of each list (duplicate endpoints)
        hull = lower[:-1] + upper[:-1]
        # If all points collinear, hull may include duplicates; ensure uniqueness in order
        seen = set()
        ordered = []
        for x, y, idx in hull:
            if idx not in seen:
                ordered.append(idx)
                seen.add(idx)
        return ordered

    def build_disjoint_matching(P, K, near_first=True, hull_pref_for_far=True):
        """Select a globally disjoint matching alternating near and far pairs.

        Far-pair selection prefers convex hull pairs when enabled, focusing contraction on diameter directions.
        Returns:
            selected_near: list of (i,j,dist) near pairs
            selected_far: list of (i,j,dist) far pairs
        """
        n = len(P)
        D2 = pairwise_sqdist(P)
        # Build list of upper triangle pairs with distances
        pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                pairs.append((i, j, math.sqrt(float(D2[i, j]))))
        # Sort near ascending by distance, far descending
        near_sorted = sorted(pairs, key=lambda x: x[2])
        far_sorted = sorted(pairs, key=lambda x: x[2], reverse=True)

        # Convex hull preference for far pairs
        hull_idxs = convex_hull_indices(P) if hull_pref_for_far else []
        hull_set = set(hull_idxs)
        far_sorted_hull = [p for p in far_sorted if (p[0] in hull_set and p[1] in hull_set)]

        used = np.zeros(n, dtype=bool)
        sel_near, sel_far = [], []
        idx_near, idx_far_hull, idx_far_all = 0, 0, 0
        total_needed = 2 * K
        toggle_near = near_first

        while (len(sel_near) + len(sel_far)) < total_needed:
            if toggle_near:
                # Find next disjoint near pair
                found = False
                while idx_near < len(near_sorted):
                    i, j, d = near_sorted[idx_near]
                    idx_near += 1
                    if not used[i] and not used[j]:
                        sel_near.append((i, j, d))
                        used[i] = True
                        used[j] = True
                        found = True
                        break
                if not found:
                    toggle_near = False
                    continue
            else:
                # Prefer far pairs among hull; fall back to all pairs
                found = False
                # First, hull-based
                while idx_far_hull < len(far_sorted_hull):
                    i, j, d = far_sorted_hull[idx_far_hull]
                    idx_far_hull += 1
                    if not used[i] and not used[j]:
                        sel_far.append((i, j, d))
                        used[i] = True
                        used[j] = True
                        found = True
                        break
                # Fallback: global far list
                if not found:
                    while idx_far_all < len(far_sorted):
                        i, j, d = far_sorted[idx_far_all]
                        idx_far_all += 1
                        if not used[i] and not used[j]:
                            sel_far.append((i, j, d))
                            used[i] = True
                            used[j] = True
                            found = True
                            break
                if not found:
                    toggle_near = True
                    continue
            # Alternate
            toggle_near = not toggle_near
            # Stop if lists exhausted or all points used
            if (idx_near >= len(near_sorted) and idx_far_all >= len(far_sorted)) or np.all(used):
                break
        return sel_near, sel_far

    def matching_step(P, beta_w, K, gamma_near, gamma_far, start_near=True):
        """Apply a targeted disjoint matching step.

        For selected far pairs: move points toward each other (contract).
        For selected near pairs: move points apart (repel).
        Weights are derived from softmax (far) and softmin (near), then renormalized per slice.
        """
        n = len(P)
        D2 = pairwise_sqdist(P)
        # Build disjoint matching with hull preference for far pairs
        sel_near, sel_far = build_disjoint_matching(P, K=K, near_first=start_near, hull_pref_for_far=True)

        # Compute soft weights over all pairs for near (softmin) and far (softmax)
        D2_off = D2.copy()
        np.fill_diagonal(D2_off, -np.inf)
        maxD2 = np.max(D2_off[np.isfinite(D2_off)])
        minD2 = np.min(D2_off[np.isfinite(D2_off)])

        W_far = np.exp(beta_w * (D2 - maxD2))
        np.fill_diagonal(W_far, 0.0)
        W_near = np.exp(beta_w * (minD2 - D2))
        np.fill_diagonal(W_near, 0.0)

        # Extract weights for selected pairs and renormalize per slice
        weights_near = [W_near[i, j] for i, j, _d in sel_near]
        sum_near = sum(weights_near) if weights_near else 1.0
        weights_near = [w / sum_near for w in weights_near]

        weights_far = [W_far[i, j] for i, j, _d in sel_far]
        sum_far = sum(weights_far) if weights_far else 1.0
        weights_far = [w / sum_far for w in weights_far]

        # Accumulate directed updates
        delta = np.zeros_like(P)
        # Near: repulsive
        for (i, j, d), w in zip(sel_near, weights_near):
            if d < 1e-12:
                theta = rng.uniform(0.0, 2.0 * math.pi)
                u = np.array([math.cos(theta), math.sin(theta)], dtype=np.float64)
            else:
                u = (P[i] - P[j]) / d
            step = gamma_near * w
            delta[i] += step * u
            delta[j] -= step * u
        # Far: contractive
        for (i, j, d), w in zip(sel_far, weights_far):
            if d < 1e-12:
                theta = rng.uniform(0.0, 2.0 * math.pi)
                u = np.array([math.cos(theta), math.sin(theta)], dtype=np.float64)
            else:
                u = (P[i] - P[j]) / d
            step = gamma_far * w
            delta[i] -= step * u
            delta[j] += step * u

        # Apply, project, renormalize
        P_new = P + delta
        P_new = project_min_distance(P_new, 1.0, max_passes=5)
        P_new = normalize_min_distance(P_new, 1.0)
        return P_new

    def refine_candidate(P_init, steps=210, beta_start=12.0, beta_max=180.0, jitter_schedule=True):
        """Refine a candidate via alternating smooth-max descent and disjoint matching steps,
        protected by a strict monotone gate on D_max^2 under D_min = 1."""
        P = P_init.copy()
        # Normalize initial configuration
        P = normalize_min_distance(P, 1.0)
        # Initialize variables
        eta = 0.075
        momentum = 0.88
        v = np.zeros_like(P)
        beta = beta_start
        best_P = P.copy()
        best_ratio2 = compute_ratio_squared(P)
        no_improve = 0
        gate_tol = 1e-9

        for t in range(steps):
            # Ensure min distance and centering
            P = normalize_min_distance(P, 1.0)

            # Current objective (normalized -> ratio_squared = D_max^2)
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

            if new_ratio2 <= current_ratio2 - gate_tol:
                # Accept improvement
                P = P_prop
                v = v_new
                eta = min(0.22, eta * 1.03)  # slight increase to accelerate
                no_improve = 0
                # Track best
                if new_ratio2 < best_ratio2:
                    best_ratio2 = new_ratio2
                    best_P = P.copy()
            else:
                # Backtrack: reduce step size and damp momentum, retry once
                eta_bt = max(1e-4, eta * 0.5)
                v_bt = v * 0.5
                v_try = momentum * v_bt - eta_bt * g
                P_try = P + v_try
                P_try = project_min_distance(P_try, 1.0, max_passes=6)
                P_try = normalize_min_distance(P_try, 1.0)
                r2_try = compute_ratio_squared(P_try)
                if r2_try <= current_ratio2 - gate_tol:
                    P = P_try
                    v = v_try
                    eta = eta_bt
                    no_improve = 0
                    if r2_try < best_ratio2:
                        best_ratio2 = r2_try
                        best_P = P.copy()
                else:
                    # Reject move; keep P, damp v, shrink eta
                    eta = eta_bt
                    v = v_bt
                    no_improve += 1
                    # Occasional escape: accept slightly worse + jitter after prolonged stagnation
                    if no_improve >= 8 and rng.uniform() < 0.08:
                        P = P_prop
                        v = v_new
                        P = P + rng.normal(scale=0.01, size=P.shape)
                        P = project_min_distance(P, 1.0, max_passes=4)
                        P = normalize_min_distance(P, 1.0)
                        no_improve = 0

            # Alternating disjoint matching step (targeted move on extreme pairs)
            # Annealed parameters
            phase = (t + 1) / float(steps)
            K = 3 + min(2, int(phase * 2.4))  # grows from 3 to 5
            start_near = True if phase < 0.55 else False  # early: protect near; late: shrink far
            # Weights beta for softmin/softmax
            beta_w = min(beta * 0.75, 110.0)
            # Annealed strengths: far increases, near decreases
            gamma_base = 0.035
            gamma_far = gamma_base * (1.0 + 1.15 * phase)  # ~0.035 -> ~0.08
            gamma_near = gamma_base * (1.0 - 0.62 * phase)  # ~0.035 -> ~0.013
            P_match = matching_step(P, beta_w=beta_w, K=K, gamma_near=gamma_near, gamma_far=gamma_far, start_near=start_near)
            r2_match = compute_ratio_squared(P_match)
            if r2_match < compute_ratio_squared(P) - gate_tol:
                P = P_match
                no_improve = 0
                if r2_match < best_ratio2:
                    best_ratio2 = r2_match
                    best_P = P.copy()
            else:
                # Escape only after stagnation; keep strictly monotone otherwise
                if rng.uniform() < 0.03 and no_improve >= 6:
                    P = P_match
                    P = project_min_distance(P, 1.0, max_passes=4)
                    P = normalize_min_distance(P, 1.0)
                    no_improve = 0

            # Anneal beta to sharpen approximation to max distance
            # Mild continuous anneal with occasional boosts
            beta = min(beta_max, beta * (1.0 + 0.006))
            if (t + 1) % 28 == 0:
                beta = min(beta_max, beta * 1.12)

            # Scheduled small jitter with decreasing variance to maintain diversity
            if jitter_schedule and (t + 1) % 32 == 0:
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

    def hex_lattice_seed_rotated(theta, include_origin=True):
        """Rotated triangular lattice with unit nearest-neighbor distance; 16 closest points."""
        R = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]], dtype=np.float64)
        base = hex_lattice_seed(include_origin=include_origin)
        return (base @ R.T)

    def rings_seed():
        """Concentric rings: 1 center + 6 on ring r1 + 9 on ring r2, spaced equally."""
        pts = []
        # Center
        pts.append([0.0, 0.0])
        # Inner ring with chord ~ 1
        m1 = 6
        r1 = 1.0
        offset1 = rng.uniform(0.0, 2.0 * math.pi)
        for k in range(m1):
            theta = offset1 + 2.0 * math.pi * k / m1
            pts.append([r1 * math.cos(theta), r1 * math.sin(theta)])
        # Outer ring with chord near 1
        m2 = 9
        r2 = 1.45  # 2*r2*sin(pi/9) ~ 1
        offset2 = rng.uniform(0.0, 2.0 * math.pi)
        for k in range(m2):
            theta = offset2 + 2.0 * math.pi * k / m2
            pts.append([r2 * math.cos(theta), r2 * math.sin(theta)])
        return np.array(pts[:16], dtype=np.float64)

    def rings_seed_alt():
        """Two-ring template: 7+9 without center, radii tuned for chord ~1."""
        pts = []
        # Inner ring
        m1 = 7
        r1 = 1.1  # 2*r1*sin(pi/7) ~ 1
        offset1 = rng.uniform(0.0, 2.0 * math.pi)
        for k in range(m1):
            theta = offset1 + 2.0 * math.pi * k / m1
            pts.append([r1 * math.cos(theta), r1 * math.sin(theta)])
        # Outer ring
        m2 = 9
        r2 = 1.45
        offset2 = rng.uniform(0.0, 2.0 * math.pi)
        for k in range(m2):
            theta = offset2 + 2.0 * math.pi * k / m2
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

    # Rotated hex lattice seeds under various rotations (0..pi/6)
    for ang in np.linspace(0.0, math.pi / 6.0, num=3, endpoint=False):
        population.append(hex_lattice_seed_rotated(ang, include_origin=True))
    for ang in np.linspace(0.0, math.pi / 6.0, num=2, endpoint=False):
        population.append(hex_lattice_seed_rotated(ang + 0.03, include_origin=False))

    # Rings, alt rings and spiral
    population.append(rings_seed())
    population.append(rings_seed_alt())
    population.append(spiral_seed())

    # Poisson-disk random seeds
    for _ in range(3):
        population.append(poisson_disk_seed(R=2.35))

    # Jittered hex variants
    for _ in range(3):
        base = hex_lattice_seed(include_origin=True)
        noise = rng.normal(scale=0.05, size=base.shape)
        population.append(base + noise)

    # Evolutionary outer loop
    pop_size = min(16, len(population))
    population = population[:pop_size]
    best_overall_P = None
    best_overall_ratio2 = float("inf")

    generations = 8
    for gen in range(generations):
        # Refine each candidate a moderate number of steps
        refined = []
        for idx in range(len(population)):
            P0 = population[idx]
            # Slight pre-jitter per generation to diversify
            P0 = P0 + rng.normal(scale=0.02, size=P0.shape)
            P_ref, r2 = refine_candidate(P0, steps=210 + 10 * (gen % 2), beta_start=12.0 + 2.0 * gen, beta_max=180.0)
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

    # Final refinement on the best to polish under strict gate
    best_overall_P, best_overall_ratio2 = refine_candidate(best_overall_P, steps=260, beta_start=20.0, beta_max=200.0)

    return best_overall_P.astype(np.float64), float(best_overall_ratio2)
# EVOLVE_END


if __name__ == "__main__":
    points, ratio_squared = optimize_construct(16, 2)
    print(json.dumps({"points": points.tolist(), "ratio_squared": ratio_squared}))
```
