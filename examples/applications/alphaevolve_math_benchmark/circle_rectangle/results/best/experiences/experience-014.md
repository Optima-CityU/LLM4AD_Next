Design choices and cadences that reliably monetize geometric slack while maintaining strict feasibility and monotone Σr improvements.

- LP-only preselection with warm starts: For each candidate center set, solve the fixed-center LP with ε_lp_pre ≈ 1e−12 and wall caps m_i, rank by Σr_lp, keep the top K ≈ 3, and warm-start survivors’ radii with the post-repair LP solution, which pushes the search into higher-potential basins identified by anisotropic variants.
- Adaptive LP cadence inside the trust-region stage: Run the exact fixed-center LP every K iterations (K initially ≈ 50; if Σr does not improve or near-active constraints increase by ≥10%, set K ← max(20, floor(0.6·K)); otherwise set K ← min(60, ceil(1.1·K))), and adopt only if Σr increases by > 1e−14, converting emerging slack promptly while avoiding unnecessary solves.
- Dual centered uniform inflation gates with immediate LP captures: Uniformly scale centers and radii about (0.5, 0.5) by the maximal t ≥ 1 that keeps wall slacks ≥ ε_wall ≈ 1e−12 (pair slacks do not decrease under this scaling), accept only if Σr increases, then immediately run a fixed-center LP capture at ε_lp ≈ 1e−12 followed by a short mid-reorder greedy tidy pass to consolidate gains.
- LP–mini-barrier–LP sandwich: Finish with an exact LP, then a short interior log-barrier ascent maximizing Σr + μ Σ log(slack) with μ ∈ {5e−4, 2e−4, 1e−4} and lazy k-NN pair augmentation, followed by a final LP capture at ε_lp ≈ 1e−12, which removes structured slack near tangencies and yields strict feasibility.
- HexLP-Trust++ with Dual Uniform Gates and Mid-Reorder Greedy: On N = 21, the method achieved sum_radii = 2.355411291642873 with validity = 1.0 and empirically adds 0.001–0.01 to Σr over either parent while preserving the perimeter ≤ 4 guarantee.

```python
#!/usr/bin/env python3
"""AP-HexGrow+: Pack 21 disjoint circles in a unit square (perimeter 4) maximizing sum of radii.

This implementation treats the 21-circle packing as a smooth constrained optimization
with multiple coordinated stages:

- Multi-start staggered hex seeding with full five-row cycling and tiny deterministic jitter,
  augmented with two anisotropic center variants per seed.
- LP-only preselection: fixed-center LP per candidate to warm-start radii, keep top-K survivors.
- Penalty warm-up (short) to regularize infeasibility of the warm starts.
- Trust-region growth-and-move stage with adaptive LP cadence: gently move centers away from
  near-active constraints while increasing radii by a fraction of the tightest slack; every K
  iterations, run a fixed-center LP reallocation with strict Σr acceptance; adapt K based on progress.
- Project-and-scale to tightly fit [0,1]^2, followed by a tidy greedy inflation pass (mid-pass reorder).
- Centered uniform inflation Gate 1 about (0.5,0.5), immediately captured by a fixed-center LP
  (strict Σr acceptance and repair).
- Continue trust-region growth-and-move with adaptive LP cadence for several blocks.
- Centered uniform inflation Gate 2 + immediate LP capture (strict Σr acceptance).
- Endgame LP–mini-barrier–LP sandwich: exact LP warm start, short interior log-barrier ascent on a
  sparse k-NN pair set with lazy augmentation, then a final LP capture.
- Strict feasibility cementing (projection, overlap repair, tiny shrink) and survivor selection.

The output is a JSON object with key "circles" mapping to an array of 21 triples [x, y, r].
"""

import json
from typing import Tuple, Optional, List, Set

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 21):
    """Construct a packing of num_circles (default 21) circles inside the unit square.

    The unit square has perimeter 4, satisfying the circumscribing rectangle perimeter
    constraint. The method maximizes the sum of radii under non-overlap via a combined
    pipeline described in the module docstring.

    Returns
    -------
    circles : ndarray of shape (num_circles, 3)
        Each row is [x, y, r] for a circle center and radius inside [0,1]^2.
    """
    N = int(num_circles)
    assert N == 21, "This solver is tuned for N=21."
    rng = np.random.RandomState(42)

    # Small positive epsilons to preserve strict feasibility and numerical slack.
    eps_wall = 1e-12
    eps_lp = 1e-12
    eps_lp_pre = 1e-12
    accept_tol = 1e-14

    # Preselection keep-top-K
    preselect_K = 3
    # Anisotropic scaling variants about (0.5, 0.5)
    anisotropic_scales = [(1.03, 0.97), (0.97, 1.03)]

    # Full five-row patterns that sum to 21. Cycling increases diversity.
    row_count_patterns = [
        np.array([5, 4, 4, 4, 4], dtype=int),
        np.array([4, 5, 4, 4, 4], dtype=int),
        np.array([4, 4, 5, 4, 4], dtype=int),
        np.array([4, 4, 4, 5, 4], dtype=int),
        np.array([4, 4, 4, 4, 5], dtype=int),
    ]

    def seed_centers(row_counts: np.ndarray, jitter_scale: float = 0.002) -> Tuple[np.ndarray, np.ndarray]:
        """Seed centers in a staggered hexagonal layout with alternating per-row offsets.

        For row j (0-indexed), x-positions are:
            x_jk = (k + 0.5 + 0.45*(j % 2)) / c_j
        y positions are equally spaced by row. Clamp to [0,1]. Apply tiny deterministic jitter
        to break symmetry and clamp again.

        Parameters
        ----------
        row_counts : ndarray of shape (rows,)
            Number of circles in each horizontal row; must sum to N.
        jitter_scale : float
            Uniform jitter magnitude applied to x and y (deterministic RNG).

        Returns
        -------
        x, y : ndarrays of shape (N,)
            Seeded center coordinates in [0,1]^2.
        """
        rows = int(len(row_counts))
        xs = []
        ys = []
        for j in range(rows):
            c = int(row_counts[j])
            y = (j + 0.5) / rows
            base = np.arange(c, dtype=float)
            x_positions = (base + 0.5 + 0.45 * (j % 2)) / float(c)
            x_positions = np.clip(x_positions, 0.0, 1.0)
            y_positions = np.full(c, y, dtype=float)
            xs.append(x_positions)
            ys.append(y_positions)
        x = np.concatenate(xs)
        y = np.concatenate(ys)
        if jitter_scale > 0.0:
            x = np.clip(x + rng.uniform(-jitter_scale, jitter_scale, size=x.shape), 0.0, 1.0)
            y = np.clip(y + rng.uniform(-jitter_scale, jitter_scale, size=y.shape), 0.0, 1.0)
        return x, y

    def anisotropic_variant(x: np.ndarray, y: np.ndarray, ax: float, ay: float, cx: float = 0.5, cy: float = 0.5) -> Tuple[np.ndarray, np.ndarray]:
        """Scale centers about (cx,cy) anisotropically by (ax, ay) and clip to [0,1]."""
        x_new = cx + ax * (x - cx)
        y_new = cy + ay * (y - cy)
        x_new = np.clip(x_new, 0.0, 1.0)
        y_new = np.clip(y_new, 0.0, 1.0)
        return x_new, y_new

    def penalty_objective_and_grad(x, y, r, lam1: float, lam2: float):
        """Compute objective and gradient for penalty warm-up.

        Objective: sum(r)
                   - lam1 * sum over i of boundaryReLU^2
                   - lam2 * sum over i<j of overlapReLU^2,
        where boundaryReLU terms are:
            s1_i = r_i - x_i     (violates left boundary)
            s2_i = r_i - y_i     (violates bottom boundary)
            s3_i = x_i + r_i - 1 (violates right boundary)
            s4_i = y_i + r_i - 1 (violates top boundary)
        and overlap term s_ij = (r_i + r_j) - d_ij, with d_ij center distance.
        """
        Nloc = x.shape[0]
        obj = float(np.sum(r))
        gx = np.zeros(Nloc, dtype=float)
        gy = np.zeros(Nloc, dtype=float)
        gr = np.ones(Nloc, dtype=float)

        # Boundary penalties
        s1 = r - x
        s2 = r - y
        s3 = x + r - 1.0
        s4 = y + r - 1.0
        s1p = np.maximum(0.0, s1)
        s2p = np.maximum(0.0, s2)
        s3p = np.maximum(0.0, s3)
        s4p = np.maximum(0.0, s4)

        if lam1 > 0:
            obj -= lam1 * (np.sum(s1p * s1p) + np.sum(s2p * s2p) + np.sum(s3p * s3p) + np.sum(s4p * s4p))
            # s1 = r - x
            mask = s1 > 0
            gr[mask] += -lam1 * 2.0 * s1[mask]
            gx[mask] += -lam1 * 2.0 * s1[mask] * (-1.0)
            # s2 = r - y
            mask = s2 > 0
            gr[mask] += -lam1 * 2.0 * s2[mask]
            gy[mask] += -lam1 * 2.0 * s2[mask] * (-1.0)
            # s3 = x + r - 1
            mask = s3 > 0
            gx[mask] += -lam1 * 2.0 * s3[mask]
            gr[mask] += -lam1 * 2.0 * s3[mask]
            # s4 = y + r - 1
            mask = s4 > 0
            gy[mask] += -lam1 * 2.0 * s4[mask]
            gr[mask] += -lam1 * 2.0 * s4[mask]

        # Pairwise overlap penalties
        if lam2 > 0:
            eps = 1e-12
            for i in range(Nloc):
                xi, yi, ri = x[i], y[i], r[i]
                for j in range(i + 1, Nloc):
                    dx = xi - x[j]
                    dy = yi - y[j]
                    dij = float(np.sqrt(dx * dx + dy * dy) + eps)
                    sij = (ri + r[j]) - dij  # positive when overlapping
                    if sij > 0.0:
                        obj -= lam2 * (sij * sij)
                        gr[i] += -lam2 * 2.0 * sij
                        gr[j] += -lam2 * 2.0 * sij
                        dsdx_i = -(dx) / dij
                        dsdy_i = -(dy) / dij
                        dsdx_j = -dsdx_i
                        dsdy_j = -dsdy_i
                        gx[i] += -lam2 * 2.0 * sij * dsdx_i
                        gy[i] += -lam2 * 2.0 * sij * dsdy_i
                        gx[j] += -lam2 * 2.0 * sij * dsdx_j
                        gy[j] += -lam2 * 2.0 * sij * dsdy_j

        return obj, gx, gy, gr

    def gradient_ascent(x, y, r):
        """Short penalty-based gradient ascent warm-up to regularize infeasibility."""
        # A lighter schedule than a full penalty solve; the trust-region does the heavy lifting.
        schedule = [10.0, 100.0, 1e3, 1e4]
        base_alpha = 0.25
        max_iters = 80  # per stage
        for lam in schedule:
            alpha = base_alpha
            stall = 0
            for _ in range(max_iters):
                obj, gx, gy, gr = penalty_objective_and_grad(x, y, r, lam, lam)
                g = np.concatenate([gx, gy, gr], axis=0)
                gnorm = float(np.linalg.norm(g))
                if not np.isfinite(obj) or not np.isfinite(gnorm):
                    break
                scale = max(1.0, gnorm / (3.0 * len(x)))
                step = alpha / scale
                g_clip = np.clip(g, -5.0, 5.0)
                ok = False
                for _ in range(10):
                    xn = np.clip(x + step * g_clip[: len(x)], 0.0, 1.0)
                    yn = np.clip(y + step * g_clip[len(x) : 2 * len(x)], 0.0, 1.0)
                    rn = np.clip(r + step * g_clip[2 * len(x) : 3 * len(x)], 0.0, 0.6)
                    obj_n, _, _, _ = penalty_objective_and_grad(xn, yn, rn, lam, lam)
                    if obj_n >= obj:
                        x, y, r = xn, yn, rn
                        ok = True
                        break
                    step *= 0.5
                stall = stall + (0 if ok else 1)
                if stall >= 5:
                    break
        return x, y, r

    def inflate_radii(x, y, r, passes: int = 6, tol: float = 1e-9):
        """Greedy deterministic inflation with centers fixed: grow each circle until
        it touches a neighbor or boundary. Repeats several passes.

        Mid-pass reorder: in each pass, after processing about half the circles,
        recompute potentials for the remainder and stable re-sort to monetize newly
        released slack.
        """
        Nloc = len(r)

        def rmax_for(i: int) -> float:
            xi, yi = x[i], y[i]
            rmax_i = min(xi, yi, 1.0 - xi, 1.0 - yi)
            for j in range(Nloc):
                if j == i:
                    continue
                dx = xi - x[j]
                dy = yi - y[j]
                dij = np.hypot(dx, dy)
                cap = dij - r[j]
                if cap < rmax_i:
                    rmax_i = cap
                    if rmax_i <= 0:
                        break
            return rmax_i

        for _ in range(passes):
            # Compute potentials at the start of the pass (based on current r)
            potentials = np.empty(Nloc, dtype=float)
            for i in range(Nloc):
                potentials[i] = rmax_for(i) - r[i]

            # Initial greedy order: largest potential first; stable argsort
            order = list(np.argsort(-potentials, kind="mergesort"))
            half = (Nloc + 1) // 2
            first = order[:half]
            rest = order[half:]

            grew = False

            # Process first half
            for idx in first:
                rmax = rmax_for(idx)
                target = max(0.0, rmax - 1e-9)
                if target > r[idx] + tol:
                    r[idx] = target
                    grew = True

            # Mid-pass reorder for remaining indices based on updated radii
            if rest:
                pot_rest = np.array([rmax_for(i) - r[i] for i in rest], dtype=float)
                # stable sort indices of rest by decreasing potential
                rest_order = np.argsort(-pot_rest, kind="mergesort")
                rest_sorted = [rest[k] for k in rest_order]
                for idx in rest_sorted:
                    rmax = rmax_for(idx)
                    target = max(0.0, rmax - 1e-9)
                    if target > r[idx] + tol:
                        r[idx] = target
                        grew = True

            if not grew:
                break
        return np.clip(r, 0.0, 0.6)

    def project_and_scale_to_unit(x, y, r):
        """Affine translate and isotropically scale so bounding rectangle fits in [0,1]^2,
        saturating at least one side."""
        minx = np.min(x - r)
        miny = np.min(y - r)
        maxx = np.max(x + r)
        maxy = np.max(y + r)
        width = maxx - minx
        height = maxy - miny
        if width <= 0 or height <= 0:
            return x, y, r
        s = min(1.0 / width, 1.0 / height)
        x2 = (x - minx) * s
        y2 = (y - miny) * s
        r2 = r * s
        x2 = np.clip(x2, 0.0, 1.0)
        y2 = np.clip(y2, 0.0, 1.0)
        r2 = np.clip(r2, 0.0, 0.6)
        return x2, y2, r2

    def is_feasible(x, y, r, tol: float = 1e-12) -> bool:
        """Check non-overlap and in-box constraints with tolerance."""
        if np.any(r - x > tol) or np.any(r - y > tol) or np.any(x + r - 1.0 > tol) or np.any(y + r - 1.0 > tol):
            return False
        Nloc = len(r)
        for i in range(Nloc):
            xi, yi, ri = x[i], y[i], r[i]
            for j in range(i + 1, Nloc):
                dij = np.hypot(xi - x[j], yi - y[j])
                if (ri + r[j]) - dij > tol:
                    return False
        return True

    def sum_radii(x, y, r) -> float:
        return float(np.sum(r))

    def lp_fixed_center_maximize_sum_r(x, y, r_init, eps: float = eps_lp) -> Optional[np.ndarray]:
        """Solve LP: maximize sum r subject to:
             0 <= r_i <= m_i, where m_i = min(x_i, 1−x_i, y_i, 1−y_i)
             r_i + r_j <= d_ij - eps for all i<j
        Uses SciPy HiGHS if available. Returns None on failure/unavailability.
        """
        try:
            from scipy.optimize import linprog
        except Exception:
            return None

        Nloc = len(r_init)
        m = np.minimum.reduce([x, 1.0 - x, y, 1.0 - y])
        pairs = []
        for i in range(Nloc):
            for j in range(i + 1, Nloc):
                dij = float(np.hypot(x[i] - x[j], y[i] - y[j]))
                pairs.append((i, j, dij))
        num_pairs = len(pairs)
        A = np.zeros((num_pairs, Nloc), dtype=float)
        b = np.zeros(num_pairs, dtype=float)
        for k, (i, j, dij) in enumerate(pairs):
            A[k, i] = 1.0
            A[k, j] = 1.0
            b[k] = max(0.0, dij - eps)
        c = -np.ones(Nloc, dtype=float)
        bounds = [(0.0, float(m[i])) for i in range(Nloc)]

        try:
            res = linprog(c, A_ub=A, b_ub=b, bounds=bounds, method="highs", options={"presolve": True})
        except Exception:
            return None
        if res is None or not res.success or res.x is None:
            return None
        r_lp = np.array(res.x, dtype=float)
        r_lp = np.clip(r_lp, 0.0, m)
        return r_lp

    def feasibility_repair(x, y, r, eps: float = eps_lp, passes: int = 4) -> np.ndarray:
        """Tiny feasibility repair: clip to wall bounds; for any violating pair,
        reduce the larger radius by half the violation. Repeat a few passes."""
        r = r.copy()
        m = np.minimum.reduce([x, 1.0 - x, y, 1.0 - y])
        r = np.clip(r, 0.0, m)
        Nloc = len(r)
        for _ in range(passes):
            changed = False
            for i in range(Nloc):
                for j in range(i + 1, Nloc):
                    dij = float(np.hypot(x[i] - x[j], y[i] - y[j]))
                    cap = dij - eps
                    if r[i] + r[j] > cap:
                        over = (r[i] + r[j]) - cap
                        if over > 0.0:
                            if r[i] >= r[j]:
                                r[i] = max(0.0, r[i] - 0.5 * over)
                            else:
                                r[j] = max(0.0, r[j] - 0.5 * over)
                            changed = True
            if not changed:
                break
        m = np.minimum.reduce([x, 1.0 - x, y, 1.0 - y])
        r = np.clip(r, 0.0, m)
        return r

    def light_wall_projection(x, y, r, eps: float = eps_wall) -> Tuple[np.ndarray, np.ndarray]:
        """Lightly project centers to satisfy wall constraints given radii:
        clamp x to [r+eps, 1-r-eps], same for y. Minimal movement."""
        x_new = np.minimum(np.maximum(x, r + eps), 1.0 - r - eps)
        y_new = np.minimum(np.maximum(y, r + eps), 1.0 - r - eps)
        return x_new, y_new

    def lp_capture_with_gate(x, y, r, eps: float = eps_lp, accept_tol: float = accept_tol) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Run fixed-center LP and feasibility repair. Acceptance-gate by Σr.
        If LP unavailable or fails, keep current radii."""
        r_cur = r.copy()
        s_cur = sum_radii(x, y, r_cur)
        r_lp = lp_fixed_center_maximize_sum_r(x, y, r_cur, eps=eps)
        if r_lp is None:
            return x, y, r
        r_rep = feasibility_repair(x, y, r_lp, eps=eps, passes=4)
        s_new = sum_radii(x, y, r_rep)
        if s_new > s_cur + accept_tol:
            x2, y2 = light_wall_projection(x, y, r_rep, eps=eps_wall)
            r_rep2 = feasibility_repair(x2, y2, r_rep, eps=eps, passes=2)
            return x2, y2, r_rep2
        else:
            return x, y, r

    def uniform_inflation_gate_with_lp(x, y, r, eps: float = eps_wall, accept_tol: float = accept_tol):
        """Compute maximal t>=1 for uniform scaling about (0.5,0.5) that preserves walls with margin eps.
        Apply conservative t=0.999*t_max. Immediately run LP capture with acceptance gate.
        Adopt only if Σr strictly increases.
        """
        cx, cy = 0.5, 0.5
        a_left = (x - cx) - r
        a_right = (x - cx) + r
        a_bottom = (y - cy) - r
        a_top = (y - cy) + r

        t_bounds = []
        mask = a_left < 0.0
        if np.any(mask):
            t_bounds.append(np.min((cx - eps) / (-a_left[mask])))
        mask = a_right > 0.0
        if np.any(mask):
            t_bounds.append(np.min((1.0 - cx - eps) / (a_right[mask])))
        mask = a_bottom < 0.0
        if np.any(mask):
            t_bounds.append(np.min((cy - eps) / (-a_bottom[mask])))
        mask = a_top > 0.0
        if np.any(mask):
            t_bounds.append(np.min((1.0 - cy - eps) / (a_top[mask])))

        if len(t_bounds) == 0:
            t_max = np.inf
        else:
            t_max = min(t_bounds)

        if not np.isfinite(t_max) or t_max <= 1.0 + 1e-15:
            return x, y, r

        t = 0.999 * t_max
        x_cand = cx + t * (x - cx)
        y_cand = cy + t * (y - cy)
        r_cand = t * r

        s_cur = sum_radii(x, y, r)
        x_lp, y_lp, r_lp = lp_capture_with_gate(x_cand, y_cand, r_cand, eps=eps_lp, accept_tol=accept_tol)
        s_new = sum_radii(x_lp, y_lp, r_lp)
        if s_new > s_cur + accept_tol:
            return x_lp, y_lp, r_lp
        else:
            s_cand = sum_radii(x_cand, y_cand, r_cand)
            if s_cand > s_cur + accept_tol and is_feasible(x_cand, y_cand, r_cand, tol=1e-12):
                x_proj, y_proj = light_wall_projection(x_cand, y_cand, r_cand, eps=eps_wall)
                r_proj = feasibility_repair(x_proj, y_proj, r_cand, eps=eps_lp, passes=2)
                return x_proj, y_proj, r_proj
            return x, y, r

    def compute_knn_pairs(x: np.ndarray, y: np.ndarray, k: int = 6) -> List[Tuple[int, int]]:
        """Return undirected k-NN pair list as (i, j) with i<j."""
        Nloc = len(x)
        pairs: Set[Tuple[int, int]] = set()
        for i in range(Nloc):
            dx = x[i] - x
            dy = y[i] - y
            d2 = dx * dx + dy * dy
            d2[i] = np.inf
            nn_idx = np.argsort(d2)[: min(k, Nloc - 1)]
            for j in nn_idx:
                a, b = (i, j) if i < j else (j, i)
                pairs.add((a, b))
        return sorted(pairs)

    def near_active_count(x: np.ndarray, y: np.ndarray, r: np.ndarray, wall_thr: float, pair_thr: float) -> int:
        """Count near-active constraints: walls with slack < wall_thr, pairs with slack < pair_thr."""
        m = np.minimum.reduce([x, 1.0 - x, y, 1.0 - y])
        wall_slack = m - r
        cnt = int(np.sum(wall_slack < wall_thr))
        Nloc = len(x)
        for i in range(Nloc):
            for j in range(i + 1, Nloc):
                dij = np.hypot(x[i] - x[j], y[i] - y[j])
                s = dij - (r[i] + r[j])
                if s < pair_thr:
                    cnt += 1
        return cnt

    def trust_region_growth_and_move(
        x: np.ndarray,
        y: np.ndarray,
        r: np.ndarray,
        iters_total: int = 180,
        k_neighbors: int = 6,
        trust_radius: float = 0.01,
        alpha_r: float = 0.3,
        wall_thr: float = 4e-3,
        pair_thr: float = 4e-3,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Trust-region center-move and growth stage with adaptive LP cadence.

        - Increase radii by a fraction of the min slack among wall and nearest-pair slacks.
        - Move centers gently away from near-active walls and pairs within a trust radius.
        - Periodically reallocate radii exactly via fixed-center LP (strict Σr acceptance).
        - Adapt LP cadence K based on progress and near-active constraints.
        """
        Nloc = len(r)
        # Initial k-NN graph
        neighbor_pairs = compute_knn_pairs(x, y, k=k_neighbors)
        # Adaptive LP cadence
        K = 50
        it_since_lp = 0
        s_prev_block = sum_radii(x, y, r)
        na_prev = near_active_count(x, y, r, wall_thr, pair_thr)

        # Force coefficients
        beta_wall = 0.6
        beta_pair = 0.5

        for it in range(iters_total):
            # Update neighbors periodically
            if it % 20 == 0:
                neighbor_pairs = compute_knn_pairs(x, y, k=k_neighbors)

            # Compute wall slack and minimal neighbor pair slack per circle
            m = np.minimum.reduce([x, 1.0 - x, y, 1.0 - y])
            wall_slack = m - r
            min_pair_slack = np.full(Nloc, np.inf, dtype=float)
            for (i, j) in neighbor_pairs:
                dij = float(np.hypot(x[i] - x[j], y[i] - y[j]))
                sij = dij - (r[i] + r[j])
                if sij < min_pair_slack[i]:
                    min_pair_slack[i] = sij
                if sij < min_pair_slack[j]:
                    min_pair_slack[j] = sij
            min_pair_slack[min_pair_slack == np.inf] = wall_slack[min_pair_slack == np.inf]

            # Growth step for radii
            g_i = np.maximum(0.0, np.minimum(wall_slack, min_pair_slack))
            dr = alpha_r * g_i
            # Limit dr to avoid too aggressive expansion in a single iteration
            dr = np.minimum(dr, 0.5 * trust_radius)
            r = np.minimum(r + dr, m)

            # Force-based center move within trust radius
            fx = np.zeros(Nloc, dtype=float)
            fy = np.zeros(Nloc, dtype=float)

            # Wall pushes
            # Left: sL = x - r; Right: sR = 1 - x - r; Bottom: sB = y - r; Top: sT = 1 - y - r
            sL = x - r
            sR = 1.0 - x - r
            sB = y - r
            sT = 1.0 - y - r
            # Push away if slack < wall_thr
            mask = sL < wall_thr
            fx[mask] += beta_wall * (wall_thr - sL[mask]) / max(wall_thr, 1e-12)
            mask = sR < wall_thr
            fx[mask] -= beta_wall * (wall_thr - sR[mask]) / max(wall_thr, 1e-12)
            mask = sB < wall_thr
            fy[mask] += beta_wall * (wall_thr - sB[mask]) / max(wall_thr, 1e-12)
            mask = sT < wall_thr
            fy[mask] -= beta_wall * (wall_thr - sT[mask]) / max(wall_thr, 1e-12)

            # Pair pushes (symmetric)
            for (i, j) in neighbor_pairs:
                dx = x[i] - x[j]
                dy = y[i] - y[j]
                dij = float(np.hypot(dx, dy))
                if dij < 1e-15:
                    # If coincident numerically, random small split push (deterministic)
                    fx[i] += beta_pair * 0.1
                    fx[j] -= beta_pair * 0.1
                    continue
                sij = dij - (r[i] + r[j])
                if sij < pair_thr:
                    # Push apart along the line; scale with how close it is
                    mag = beta_pair * (pair_thr - sij) / max(pair_thr, 1e-12)
                    ux = dx / dij
                    uy = dy / dij
                    fx[i] += mag * ux
                    fy[i] += mag * uy
                    fx[j] -= mag * ux
                    fy[j] -= mag * uy

            # Cap movement to trust radius
            step_norm = np.maximum(1.0, np.sqrt(fx * fx + fy * fy) / max(trust_radius, 1e-12))
            dx_move = fx / step_norm
            dy_move = fy / step_norm

            # Apply move and project inside walls given current radii
            x = x + dx_move
            y = y + dy_move
            x, y = light_wall_projection(x, y, r, eps=eps_wall)

            # Light overlap repair in sparse neighbor graph
            for (i, j) in neighbor_pairs:
                dij = float(np.hypot(x[i] - x[j], y[i] - y[j]))
                over = (r[i] + r[j]) - dij
                if over > 0.0:
                    # Reduce larger radius slightly to repair
                    if r[i] >= r[j]:
                        r[i] = max(0.0, r[i] - 0.55 * over)
                    else:
                        r[j] = max(0.0, r[j] - 0.55 * over)

            # Ensure within wall bounds after repair
            m = np.minimum.reduce([x, 1.0 - x, y, 1.0 - y])
            r = np.minimum(r, m)

            # Adaptive LP cadence
            it_since_lp += 1
            if it_since_lp >= K:
                s_before = sum_radii(x, y, r)
                x_lp, y_lp, r_lp = lp_capture_with_gate(x, y, r, eps=1e-10, accept_tol=accept_tol)
                s_after = sum_radii(x_lp, y_lp, r_lp)
                if s_after > s_before + accept_tol:
                    x, y, r = x_lp, y_lp, r_lp
                    # small tidy inflation to harvest fresh slack
                    r = inflate_radii(x, y, r, passes=1, tol=1e-9)
                # Adapt K based on progress/near-actives
                na_cur = near_active_count(x, y, r, wall_thr, pair_thr)
                if s_after <= s_prev_block + accept_tol or na_cur >= int(na_prev * 1.10) + 1:
                    K = max(20, int(0.6 * K))
                else:
                    K = min(60, int(np.ceil(1.1 * K)))
                s_prev_block = sum_radii(x, y, r)
                na_prev = na_cur
                it_since_lp = 0

        # Final light projection/repair for safety
        x, y = light_wall_projection(x, y, r, eps=eps_wall)
        r = feasibility_repair(x, y, r, eps=eps_lp, passes=2)
        return x, y, r

    def barrier_objective_and_grad(x: np.ndarray, y: np.ndarray, r: np.ndarray, pairs: List[Tuple[int, int]], mu: float):
        """Compute interior log-barrier objective and gradient:
           f = sum(r) + mu * [sum_i sum_{walls} log(slack_i) + sum_(i,j) log(s_ij)]
           where wall slacks: sL=x-r, sR=1-x-r, sB=y-r, sT=1-y-r; pair slack s_ij = d_ij - (r_i + r_j).
        Returns f, gx, gy, gr.
        """
        Nloc = len(r)
        # Wall slacks
        sL = x - r
        sR = 1.0 - x - r
        sB = y - r
        sT = 1.0 - y - r
        if np.any(sL <= 0) or np.any(sR <= 0) or np.any(sB <= 0) or np.any(sT <= 0):
            return -np.inf, None, None, None

        f = float(np.sum(r))
        f += float(mu * (np.sum(np.log(sL)) + np.sum(np.log(sR)) + np.sum(np.log(sB)) + np.sum(np.log(sT))))

        gx = np.zeros(Nloc, dtype=float)
        gy = np.zeros(Nloc, dtype=float)
        gr = np.ones(Nloc, dtype=float)

        # Wall gradients
        invL = 1.0 / sL
        invR = 1.0 / sR
        invB = 1.0 / sB
        invT = 1.0 / sT
        gx += mu * (invL - invR)
        gy += mu * (invB - invT)
        gr += mu * (-(invL + invR + invB + invT))

        # Pair terms
        for (i, j) in pairs:
            dx = x[i] - x[j]
            dy = y[i] - y[j]
            dij = float(np.hypot(dx, dy))
            # add small epsilon to avoid zero divisions
            if dij < 1e-15:
                return -np.inf, None, None, None
            sij = dij - (r[i] + r[j])
            if sij <= 0:
                return -np.inf, None, None, None
            f += mu * np.log(sij)
            invs = mu / sij
            ux = dx / dij
            uy = dy / dij
            gx[i] += invs * ux
            gy[i] += invs * uy
            gx[j] -= invs * ux
            gy[j] -= invs * uy
            gr[i] -= invs
            gr[j] -= invs

        return f, gx, gy, gr

    def barrier_optimize(
        x: np.ndarray,
        y: np.ndarray,
        r: np.ndarray,
        mu_list: List[float] = [5e-4, 2e-4, 1e-4],
        k_neighbors: int = 6,
        iters_per_mu: int = 50,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Short interior log-barrier ascent on (x,y,r) with sparse k-NN pair set and lazy augmentation."""
        Nloc = len(r)
        # Initial sparse pair set
        pairs = compute_knn_pairs(x, y, k=k_neighbors)
        # Ensure strict feasibility
        x, y = light_wall_projection(x, y, r, eps=eps_wall)
        r = feasibility_repair(x, y, r, eps=eps_lp, passes=3)

        for mu in mu_list:
            step0 = 0.08
            for _ in range(iters_per_mu):
                f, gx, gy, gr = barrier_objective_and_grad(x, y, r, pairs, mu)
                if not np.isfinite(f) or gx is None:
                    # Try to repair feasibility and continue
                    x, y = light_wall_projection(x, y, r, eps=eps_wall)
                    r = feasibility_repair(x, y, r, eps=eps_lp, passes=2)
                    continue
                # Gradient clipping to avoid huge steps
                g = np.concatenate([gx, gy, gr], axis=0)
                gnorm = float(np.linalg.norm(g))
                if gnorm <= 1e-14:
                    break
                scale = max(1.0, gnorm / (3.0 * Nloc))
                step = step0 / scale
                g_clip = np.clip(g, -5.0, 5.0)

                ok = False
                for _ls in range(12):
                    xn = np.clip(x + step * g_clip[:Nloc], 0.0, 1.0)
                    yn = np.clip(y + step * g_clip[Nloc:2 * Nloc], 0.0, 1.0)
                    rn = np.clip(r + step * g_clip[2 * Nloc:3 * Nloc], 0.0, 0.6)
                    # maintain strict positivity of slacks
                    xn, yn = light_wall_projection(xn, yn, rn, eps=eps_wall)
                    # Evaluate candidate
                    f_n, *_ = barrier_objective_and_grad(xn, yn, rn, pairs, mu)
                    if np.isfinite(f_n) and f_n >= f:
                        x, y, r = xn, yn, rn
                        ok = True
                        break
                    step *= 0.5
                if not ok:
                    # try lazy augmentation if stalled
                    pass

                # Lazy augmentation: add any new near-violating pairs (global scan light)
                # Add if slack < 3e-5
                added = False
                for i in range(Nloc):
                    xi, yi = x[i], y[i]
                    for j in range(i + 1, Nloc):
                        dx = xi - x[j]
                        dy = yi - y[j]
                        dij = float(np.hypot(dx, dy))
                        sij = dij - (r[i] + r[j])
                        if sij < 3e-5:
                            tup = (i, j)
                            if tup not in pairs:
                                pairs.append(tup)
                                added = True
                if added:
                    # Minor repair to maintain positivity
                    r = feasibility_repair(x, y, r, eps=eps_lp, passes=1)

        # Final repair for cleanliness
        x, y = light_wall_projection(x, y, r, eps=eps_wall)
        r = feasibility_repair(x, y, r, eps=eps_lp, passes=3)
        return x, y, r

    best_circles = None
    best_sum = -1.0

    # Multiple restarts with jittered seeds and full five-row cycling patterns
    restarts = 16
    for restart in range(restarts):
        row_counts = row_count_patterns[restart % len(row_count_patterns)]
        assert int(row_counts.sum()) == N

        # Seed centers with staggered hexagonal layout
        x_base, y_base = seed_centers(row_counts, jitter_scale=0.002 if restart > 0 else 0.0)

        # Build candidate center sets: base + two anisotropic variants
        candidate_centers: List[Tuple[np.ndarray, np.ndarray]] = [(x_base, y_base)]
        for ax, ay in anisotropic_scales:
            xa, ya = anisotropic_variant(x_base, y_base, ax=ax, ay=ay, cx=0.5, cy=0.5)
            candidate_centers.append((xa, ya))

        # Preselection via LP on fixed centers; fallback to greedy if LP unavailable/fails.
        candidate_records = []  # list of tuples: (score, x, y, r_warm)
        for (xc, yc) in candidate_centers:
            r_init = np.zeros(N, dtype=float)
            r_lp = lp_fixed_center_maximize_sum_r(xc, yc, r_init, eps=eps_lp_pre)
            if r_lp is not None:
                r_rep = feasibility_repair(xc, yc, r_lp, eps=eps_lp_pre, passes=4)
                score = float(np.sum(r_rep))
                candidate_records.append((score, xc.copy(), yc.copy(), r_rep.copy()))
            else:
                r_greedy = inflate_radii(xc, yc, r_init.copy(), passes=1, tol=1e-9)
                r_rep = feasibility_repair(xc, yc, r_greedy, eps=eps_lp_pre, passes=2)
                score = float(np.sum(r_rep))
                candidate_records.append((score, xc.copy(), yc.copy(), r_rep.copy()))

        # Select top-K candidates by score
        candidate_records.sort(key=lambda t: -t[0])
        survivors = candidate_records[: max(1, preselect_K)]

        # Run the pipeline for each survivor, warm-starting radii with r_warm
        for (_, x0, y0, r_warm) in survivors:
            # Penalty-based warm-up with warm start radii
            x_opt, y_opt, r_opt = gradient_ascent(x0.copy(), y0.copy(), r_warm.copy())

            # First trust-region growth-and-move block with adaptive LP cadence
            x_opt, y_opt, r_opt = trust_region_growth_and_move(
                x_opt, y_opt, r_opt, iters_total=180, k_neighbors=6, trust_radius=0.01, alpha_r=0.3, wall_thr=4e-3, pair_thr=4e-3
            )

            # Project into unit square and scale to saturate at least one side
            x_opt, y_opt, r_opt = project_and_scale_to_unit(x_opt, y_opt, r_opt)

            # Tidy inflation after global projection
            r_opt = inflate_radii(x_opt, y_opt, r_opt, passes=2, tol=1e-9)

            # LP polish A (post-greedy, pre-uniform)
            x_opt, y_opt, r_opt = lp_capture_with_gate(x_opt, y_opt, r_opt, eps=eps_lp, accept_tol=accept_tol)

            # Uniform inflation Gate 1 + LP capture
            x_opt, y_opt, r_opt = uniform_inflation_gate_with_lp(x_opt, y_opt, r_opt, eps=eps_wall, accept_tol=accept_tol)

            # Continue trust-region growth with adaptive LP cadence (shorter)
            x_opt, y_opt, r_opt = trust_region_growth_and_move(
                x_opt, y_opt, r_opt, iters_total=120, k_neighbors=6, trust_radius=0.008, alpha_r=0.28, wall_thr=3e-3, pair_thr=3e-3
            )

            # Uniform inflation Gate 2 + LP capture
            x_opt, y_opt, r_opt = uniform_inflation_gate_with_lp(x_opt, y_opt, r_opt, eps=eps_wall, accept_tol=accept_tol)

            # Endgame LP–mini-barrier–LP sandwich
            # LP warm-start
            x_opt, y_opt, r_opt = lp_capture_with_gate(x_opt, y_opt, r_opt, eps=eps_lp, accept_tol=accept_tol)
            # Mini barrier ascent with lazy pair augmentation
            x_opt, y_opt, r_opt = barrier_optimize(x_opt, y_opt, r_opt, mu_list=[5e-4, 2e-4, 1e-4], k_neighbors=6, iters_per_mu=50)
            # Final LP capture
            x_opt, y_opt, r_opt = lp_capture_with_gate(x_opt, y_opt, r_opt, eps=eps_lp, accept_tol=accept_tol)

            # Final feasibility cementing: pairwise repair + light wall projection + tiny uniform shrink
            r_opt = feasibility_repair(x_opt, y_opt, r_opt, eps=eps_lp, passes=3)
            x_opt, y_opt = light_wall_projection(x_opt, y_opt, r_opt, eps=eps_wall)
            r_opt = 0.999999 * r_opt

            # Evaluate and retain best feasible solution
            if is_feasible(x_opt, y_opt, r_opt, tol=1e-12):
                total_r = sum_radii(x_opt, y_opt, r_opt)
                if total_r > best_sum:
                    best_sum = total_r
                    best_circles = np.column_stack([x_opt, y_opt, r_opt])

    # Fallback to a safe baseline if something went wrong (should not happen)
    if best_circles is None:
        radius = 0.099999
        centers = np.array(
            [[(column + 0.5) / 5, (row + 0.5) / 5] for row in range(5) for column in range(5)][:N],
            dtype=float,
        )
        radii = np.full(N, radius, dtype=float)
        best_circles = np.column_stack((centers, radii))

    return best_circles
# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```
