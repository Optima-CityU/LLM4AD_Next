Simultaneous contraction of multiple hull antipodal pairs during plateau conditions to shrink the diameter support set with safeguarded acceptance.

- HR-MLS: Hull-Restricted Multi-Pair Line-Search Contraction: When the diameter is supported by multiple convex-hull antipodal pairs with near-equal lengths (detected by rotating calipers with the top k≈3–5 hull distances within ~0.985–0.99 of the maximum or no_improve ≥ 2), HR-MLS simultaneously contracts a disjoint set of M=3–5 hull pairs via a backtracking line search over τ ∈ {0.06, 0.04, 0.025, 0.015} with congestion-aware scaling s_ij ∈ [0.5, 1.2], immediately re-projects and normalizes to restore D_min = 1, and accepts only if R^2 strictly decreases; restricting to hull pairs reduces bottleneck swapping across near-equal far pairs and, in Generation 11, yielded ratio_squared = 12.896309978578525 with validity = 1.0 and score = 0.9994538075937826.

```python
#!/usr/bin/env python3
"""16-point planar construction minimizing max/min distance ratio via hybrid CAP-LS.

This implementation targets minimizing R^2 = (D_max / D_min)^2 for n=16, d=2.

Key features:
- Scale-invariant normalization: every iterate is re-centered and scaled so D_min = 1,
  reducing the objective to R^2 = D_max^2.
- Smooth-max (log-sum-exp) descent on squared pairwise distances to shrink the diameter.
- Projection operator enforcing the hard min-distance constraint by pushing violating pairs apart.
  MUTATION: congestion-aware split for violating pair corrections to curb diameter growth.
- Alternating globally disjoint matching step targeting extreme (near/far) pairs with
  directed repulsive/contractive moves, weights derived from softmin/softmax, and annealed strengths.
- Targeted safeguarded farthest-pair line-search contraction (CAP-LS): directly contracts
  the current farthest pair with a short backtracking line-search, congestion-aware step scaling.
- NEW: Convex-hull–restricted multi-pair line-search contraction (HR-MLS) that contracts
  multiple disjoint hull antipodal pairs simultaneously during plateaus.
- Diverse seeding: hex-lattice shells (with rotations), rings, Poisson-disk, spiral, plus jittered variants.
- Evolutionary outer loop maintaining diversity and polishing elites.

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

    def dmin_dmax_with_indices(P):
        """Return (dmin, dmax, i_max, j_max) where i_max,j_max is farthest pair indices."""
        D2 = pairwise_sqdist(P)
        np.fill_diagonal(D2, np.nan)
        # dmin, dmax
        dmin2 = np.nanmin(D2)
        dmax2 = np.nanmax(D2)
        # farthest pair indices
        i_max, j_max = np.unravel_index(np.nanargmax(D2), D2.shape)
        return math.sqrt(float(dmin2)), math.sqrt(float(dmax2)), int(i_max), int(j_max)

    def compute_congestion(P, min_d=1.0, band=1.05):
        """Compute congestion cong[k] = 1 + count of neighbors within r_band = band * min_d."""
        n = len(P)
        D2 = pairwise_sqdist(P)
        r_band = band * min_d
        r_band2 = r_band * r_band
        near_band = (D2 <= r_band2)
        np.fill_diagonal(near_band, False)
        cong = 1 + near_band.sum(axis=1)
        return cong

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
        """Project points to satisfy ||pi - pj|| >= min_d by pushing violating pairs apart.

        Mutation: Use a congestion-aware split for pairwise corrections.
        For a violating pair (i, j) with distance d < min_d, compute a local congestion score
        cong[k] = 1 + count of neighbors within r_band = 1.05 * min_d for each point k.
        Then split the required total separation (min_d - d) using weights w_k = 1 / (cong[k] + eps),
        so the less-crowded endpoint moves more:
            alpha_i = w_i / (w_i + w_j), alpha_j = 1 - alpha_i.
        Corrections:
            u = unit vector from j to i
            corr[i] += alpha_i * (min_d - d) * u
            corr[j] -= alpha_j * (min_d - d) * u
        """
        n = len(P)
        eps = 1e-6
        # Iterate a few passes to resolve violations
        for _ in range(max_passes):
            D2 = pairwise_sqdist(P)
            # Identify violating pairs
            viol_mask = (~np.eye(n, dtype=bool)) & (D2 < (min_d - tol) ** 2)
            if not np.any(viol_mask):
                break

            # Compute congestion scores once per pass based on a soft neighbor band
            r_band = 1.05 * min_d
            r_band2 = r_band * r_band
            near_band = (D2 <= r_band2)
            np.fill_diagonal(near_band, False)
            cong = 1 + near_band.sum(axis=1)  # integer counts + 1 to avoid zero

            corr = np.zeros_like(P)
            # Process only upper triangle to avoid double counting
            rows, cols = np.where(np.triu(viol_mask, k=1))
            for i, j in zip(rows, cols):
                dij2 = D2[i, j]
                dij = math.sqrt(max(dij2, 0.0))
                total_need = max(0.0, min_d - dij)
                if total_need <= 0.0:
                    continue
                if dij > 1e-12:
                    # Unit direction from j to i
                    u = (P[i] - P[j]) / dij
                else:
                    # If coincident or extremely close, pick a random unit direction
                    theta = rng.uniform(0.0, 2.0 * math.pi)
                    u = np.array([math.cos(theta), math.sin(theta)], dtype=np.float64)

                # Congestion-aware split: less-crowded point moves more
                wi = 1.0 / (float(cong[i]) + eps)
                wj = 1.0 / (float(cong[j]) + eps)
                denom = wi + wj
                if not np.isfinite(denom) or denom <= eps:
                    alpha_i = 0.5
                else:
                    alpha_i = wi / denom
                alpha_i = float(np.clip(alpha_i, 0.0, 1.0))
                alpha_j = 1.0 - alpha_i

                # Apply corrections
                corr[i] += alpha_i * total_need * u
                corr[j] -= alpha_j * total_need * u

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

    def build_disjoint_matching(P, K, near_first=True):
        """Select a globally disjoint matching alternating near and far pairs.

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

        used = np.zeros(n, dtype=bool)
        sel_near, sel_far = [], []
        # Alternate selection
        idx_near, idx_far = 0, 0
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
                # If none found, try opposite list
                if not found:
                    toggle_near = False
                    continue
            else:
                # Find next disjoint far pair
                found = False
                while idx_far < len(far_sorted):
                    i, j, d = far_sorted[idx_far]
                    idx_far += 1
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
            # Stop if all points are used or lists exhausted
            if (idx_near >= len(near_sorted) and idx_far >= len(far_sorted)) or np.all(used):
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
        # Build disjoint matching
        sel_near, sel_far = build_disjoint_matching(P, K=K, near_first=start_near)

        # Compute soft weights over all pairs for near (softmin) and far (softmax)
        # Far-softmax: exp(beta * (D2 - max))
        D2_off = D2.copy()
        np.fill_diagonal(D2_off, -np.inf)
        maxD2 = np.max(D2_off[np.isfinite(D2_off)])
        W_far = np.exp(beta_w * (D2 - maxD2))
        np.fill_diagonal(W_far, 0.0)
        # Near-softmin: exp(beta * (min - D2))
        minD2 = np.min(D2_off[np.isfinite(D2_off)])
        W_near = np.exp(beta_w * (minD2 - D2))
        np.fill_diagonal(W_near, 0.0)

        # Extract weights for selected pairs and renormalize per slice
        weights_near = []
        for i, j, _d in sel_near:
            weights_near.append(W_near[i, j])
        sum_near = sum(weights_near) if weights_near else 1.0
        weights_near = [w / sum_near for w in weights_near]

        weights_far = []
        for i, j, _d in sel_far:
            weights_far.append(W_far[i, j])
        sum_far = sum(weights_far) if weights_far else 1.0
        weights_far = [w / sum_far for w in weights_far]

        # Accumulate directed updates
        delta = np.zeros_like(P)
        # Near: repulsive
        for (i, j, d), w in zip(sel_near, weights_near):
            if d < 1e-12:
                # random direction if extremely close (shouldn't happen after projection)
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
            # Move i toward j: -u; j toward i: +u
            delta[i] -= step * u
            delta[j] += step * u

        # Apply, project, renormalize
        P_new = P + delta
        P_new = project_min_distance(P_new, 1.0, max_passes=5)
        P_new = normalize_min_distance(P_new, 1.0)
        return P_new

    def farthest_pair_line_search(P, tau_list, min_d=1.0):
        """Safeguarded line search contracting the farthest pair.

        Returns:
            (P_acc, improved) where improved indicates strict improvement acceptance.
        """
        # Identify farthest pair
        _, _, i_star, j_star = dmin_dmax_with_indices(P)
        # Unit vector along line from j* to i*
        diff = P[i_star] - P[j_star]
        dij = float(np.linalg.norm(diff))
        if dij < 1e-12:
            return P, False
        u = diff / dij

        # Congestion-aware scaling of tau
        cong = compute_congestion(P, min_d=min_d, band=1.05)
        cmax = float(max(cong[i_star], cong[j_star]))
        # Map cmax (>=1) to s in [0.5, 1.2], larger when less congested
        # Here cmax=1 -> 1.2; increase cmax reduces s linearly by 0.08 per increment.
        s = 1.2 - 0.08 * max(0.0, (cmax - 1.0))
        s = float(np.clip(s, 0.5, 1.2))

        base_r2 = compute_ratio_squared(P)
        # Try candidates
        for tau in tau_list:
            tau_eff = tau * s
            P_try = P.copy()
            # Symmetric contraction
            P_try[i_star] = P_try[i_star] - tau_eff * u
            P_try[j_star] = P_try[j_star] + tau_eff * u
            # Project and renormalize to maintain min distance and scale invariance
            P_try = project_min_distance(P_try, min_d, max_passes=6)
            P_try = normalize_min_distance(P_try, min_d)
            r2_try = compute_ratio_squared(P_try)
            if r2_try < base_r2 - 1e-12:
                return P_try, True
        return P, False

    # --------------- NEW: Hull + rotating calipers + HR-MLS -----------------

    def _cross(o, a, b):
        """2D cross product z-component of OA x OB."""
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def convex_hull_indices(P):
        """Monotone chain convex hull returning indices in CCW order.

        Colinear points on edges are excluded to keep hull minimal (strict turns)."""
        n = len(P)
        pts = [(float(P[i, 0]), float(P[i, 1]), i) for i in range(n)]
        pts.sort()  # sort by x, then y
        # Remove duplicates if any
        unique = []
        seen = set()
        for x, y, idx in pts:
            key = (round(x, 15), round(y, 15))
            if key in seen:
                continue
            seen.add(key)
            unique.append((x, y, idx))
        if len(unique) <= 1:
            return [unique[0][2]] if unique else []
        # Build lower hull
        lower = []
        for x, y, idx in unique:
            while len(lower) >= 2:
                x1, y1, i1 = lower[-2]
                x2, y2, i2 = lower[-1]
                if _cross((x1, y1), (x2, y2), (x, y)) <= 0.0:
                    lower.pop()
                else:
                    break
            lower.append((x, y, idx))
        # Build upper hull
        upper = []
        for x, y, idx in reversed(unique):
            while len(upper) >= 2:
                x1, y1, i1 = upper[-2]
                x2, y2, i2 = upper[-1]
                if _cross((x1, y1), (x2, y2), (x, y)) <= 0.0:
                    upper.pop()
                else:
                    break
            upper.append((x, y, idx))
        # Concatenate without duplicate endpoints
        hull = lower[:-1] + upper[:-1]
        hull_indices = [idx for _x, _y, idx in hull]
        return hull_indices

    def rotating_calipers_antipodal_pairs(P, hull_idx):
        """Compute candidate antipodal pairs on convex hull for diameter via rotating calipers.

        Returns a list of unique pairs (i, j, d) with i<j and d = Euclidean distance."""
        m = len(hull_idx)
        if m == 0:
            return []
        if m == 1:
            return []
        if m == 2:
            i, j = hull_idx[0], hull_idx[1]
            d = float(np.linalg.norm(P[i] - P[j]))
            return [(min(i, j), max(i, j), d)]
        # Ensure CCW order
        H = hull_idx[:]  # indices in CCW
        # Start with j=1 for i=0
        antipairs = set()
        pairs = []
        j = 1
        # Helper for squared dist
        def d2(i_idx, j_idx):
            diff = P[i_idx] - P[j_idx]
            return float(diff[0] * diff[0] + diff[1] * diff[1])
        m2 = m  # polygon is closed by modulo
        for ii in range(m):
            i_idx = H[ii]
            # advance j while distance increases
            while True:
                j_next = (j + 1) % m2
                if d2(i_idx, H[j_next]) >= d2(i_idx, H[j]):
                    j = j_next
                else:
                    break
            a, b = i_idx, H[j]
            key = (min(a, b), max(a, b))
            if key not in antipairs:
                antipairs.add(key)
                pairs.append((key[0], key[1], math.sqrt(d2(a, b))))
        return pairs

    def detect_plateau_and_pairs(P, phase, no_improve):
        """Detect if the diameter is plateaued and return sorted antipodal hull pairs.

        Returns (plateaued: bool, sorted_pairs: list of (i,j,d) descending by d)."""
        hull_idx = convex_hull_indices(P)
        anti_pairs = rotating_calipers_antipodal_pairs(P, hull_idx)
        if not anti_pairs:
            # Fallback: consider farthest pair only
            _, _, i_star, j_star = dmin_dmax_with_indices(P)
            d_star = float(np.linalg.norm(P[i_star] - P[j_star]))
            return (no_improve >= 2), [(min(i_star, j_star), max(i_star, j_star), d_star)]
        # Sort pairs by distance descending
        anti_pairs_sorted = sorted(anti_pairs, key=lambda x: x[2], reverse=True)
        # Top-k band test
        # Annealed: threshold from 0.99 early to 0.985 late; k from 3 to 5
        thresh = 0.99 - 0.005 * phase
        k = 3 if phase < 0.6 else (4 if phase < 0.85 else 5)
        k = min(k, len(anti_pairs_sorted))
        if k == 0:
            return (no_improve >= 2), anti_pairs_sorted
        d_top = anti_pairs_sorted[0][2]
        plateau = False
        if d_top > 0.0 and k >= 2:
            d_k = anti_pairs_sorted[k - 1][2]
            if d_k / d_top >= thresh:
                plateau = True
        # Also consider recent stagnation
        if no_improve >= 2:
            plateau = True
        return plateau, anti_pairs_sorted

    def hr_multi_pair_line_search(P, pairs_sorted, phase, min_d=1.0):
        """Hull-restricted multi-pair contraction with backtracking line search.

        Select top M disjoint antipodal hull pairs and contract them simultaneously.

        Returns (P_new, improved: bool)."""
        n = len(P)
        # Anneal M from 3 to 5
        M = 3 + int(min(1.0, phase) * 2.0)
        M = int(np.clip(M, 1, 5))
        # Build disjoint set of top M pairs
        used = np.zeros(n, dtype=bool)
        chosen = []
        for i, j, d in pairs_sorted:
            if not used[i] and not used[j]:
                chosen.append((i, j, d))
                used[i] = True
                used[j] = True
            if len(chosen) >= M:
                break
        if not chosen:
            return P, False

        # Candidate step sizes (conservative relative to single-pair LS)
        tau_list = [0.06, 0.04, 0.025, 0.015]

        # Congestion-aware scaling for each pair s_ij in [0.5, 1.2]
        cong = compute_congestion(P, min_d=min_d, band=1.05)

        def pair_scale(i, j):
            cmax = float(max(cong[i], cong[j]))
            s = 1.2 - 0.08 * max(0.0, (cmax - 1.0))
            return float(np.clip(s, 0.5, 1.2))

        # Optional softmax weighting over pair distances to emphasize largest ones
        ds = np.array([d for (_i, _j, d) in chosen], dtype=np.float64)
        if len(ds) >= 2:
            beta_w = 8.0  # moderately sharp
            ds_norm = ds - ds.max()
            w = np.exp(beta_w * ds_norm)
            w_sum = float(w.sum())
            if w_sum <= 0.0 or not np.isfinite(w_sum):
                weights = np.ones_like(ds)
            else:
                weights = w / w_sum
            # Rescale so average ~ 1.0 to keep magnitude comparable across M
            weights = weights * len(ds)
            # Clip to avoid extreme imbalances
            weights = np.clip(weights, 0.7, 1.6)
        else:
            weights = np.ones_like(ds)

        base_r2 = compute_ratio_squared(P)

        # Try candidate tau values
        for tau in tau_list:
            P_try = P.copy()
            # Apply symmetric contraction for each selected pair
            for (k, (i, j, d)) in enumerate(chosen):
                # Direction from j to i
                diff = P[i] - P[j]
                dij = float(np.linalg.norm(diff))
                if dij < 1e-12:
                    # If degenerate, skip this pair
                    continue
                u = diff / dij
                s_ij = pair_scale(i, j)
                tau_eff = tau * s_ij * float(weights[k])
                # Symmetric move
                P_try[i] = P_try[i] - tau_eff * u
                P_try[j] = P_try[j] + tau_eff * u

            # Enforce feasibility and normalization
            P_try = project_min_distance(P_try, min_d, max_passes=6)
            P_try = normalize_min_distance(P_try, min_d)
            r2_try = compute_ratio_squared(P_try)
            if r2_try < base_r2 - 1e-12:
                return P_try, True

        return P, False

    # ----------------------- Main refinement loop ----------------------------

    def refine_candidate(P_init, steps=200, beta_start=12.0, beta_max=160.0, jitter_schedule=True):
        """Refine a candidate via alternating smooth-max descent, disjoint matching, and
        targeted farthest-pair line-search contraction (CAP-LS) augmented by HR-MLS."""
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
                    eta = max(1e-3, eta)

            # Alternating disjoint matching step (targeted move on extreme pairs)
            # Annealed parameters
            phase = (t + 1) / float(steps)
            K = 3 + min(2, int(phase * 2.1))  # grows from 3 to 5
            start_near = True if phase < 0.55 else False  # early: protect near; late: shrink far
            # Weights beta for softmin/softmax
            beta_w = min(beta * 0.75, 100.0)
            # Annealed strengths: far increases, near decreases
            gamma_base = 0.035
            gamma_far = gamma_base * (1.0 + 1.1 * phase)  # ~0.035 -> ~0.078
            gamma_near = gamma_base * (1.0 - 0.6 * phase)  # ~0.035 -> ~0.014
            P_match = matching_step(P, beta_w=beta_w, K=K, gamma_near=gamma_near, gamma_far=gamma_far, start_near=start_near)
            r2_match = compute_ratio_squared(P_match)
            if r2_match < compute_ratio_squared(P) - 1e-9:
                P = P_match
                no_improve = 0
                if r2_match < best_ratio2:
                    best_ratio2 = r2_match
                    best_P = P.copy()
            else:
                # Occasionally accept a neutral/slightly worse matching move if stuck
                if rng.uniform() < 0.05 and no_improve >= 3:
                    P = P_match

            # Plateau detection on hull antipodal pairs
            plateaued, hull_pairs_sorted = detect_plateau_and_pairs(P, phase=phase, no_improve=no_improve)

            # Hull-restricted multi-pair contraction (HR-MLS), used when plateaued or late-phase
            if plateaued or phase > 0.5:
                P_hr, improved_hr = hr_multi_pair_line_search(P, hull_pairs_sorted, phase=phase, min_d=1.0)
                if improved_hr:
                    P = P_hr
                    no_improve = 0
                    r2_now = compute_ratio_squared(P)
                    if r2_now < best_ratio2:
                        best_ratio2 = r2_now
                        best_P = P.copy()

            # Targeted D_max line-search contraction (safeguarded; congestion-aware scaling)
            # Trigger later in the schedule or when progress stalls
            if phase > 0.35 or no_improve >= 2:
                tau_list = [0.12, 0.08, 0.05, 0.03, 0.02, 0.01]
                P_ls, improved = farthest_pair_line_search(P, tau_list, min_d=1.0)
                if improved:
                    P = P_ls
                    no_improve = 0
                    r2_now = compute_ratio_squared(P)
                    if r2_now < best_ratio2:
                        best_ratio2 = r2_now
                        best_P = P.copy()

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
        r1 = 1.0  # chord length 2*r*sin(pi/m1) = 1 when r=1 for m1=6
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

    # Final refinement on the best to polish (with stronger beta and include line-search)
    best_overall_P, best_overall_ratio2 = refine_candidate(best_overall_P, steps=220, beta_start=20.0, beta_max=200.0)

    return best_overall_P.astype(np.float64), float(best_overall_ratio2)
# EVOLVE_END


if __name__ == "__main__":
    points, _ = optimize_construct(16, 2)
    print(json.dumps({"points": points.tolist()}))
```
