Actionable design insight grounded in the algorithm description, metrics, generation evidence, and implementation.

- TriHex+ LP-Bootstrap Multi-Start with Dual Uniform Inflations and LP–Mini-Barrier–LP Sandwich: The strongest decisions were an exact fixed-center LP bootstrap before barrier ascent, periodic exact LP reallocations of radii, and one or two global uniform inflations about (0.5, 0.5) each followed by LP, all guarded by acceptance gates that apply only when Σ r increases. In the 21-circle unit-square setting with strict slacks ε = 1e−6, these steps rapidly remove structured slack so the barrier focuses on relocating centers, yielding valid near-tangential packings and achieving sum_radii 2.3411715170340597 with validity 1.0 and no errors. Generation evidence shows this child slightly exceeds Parent 2 (2.3411713988448883) while preserving seed diversification (patterns including [5,5,4,4,3] and [4,5,5,4,3], spacings s ∈ {0.2×0.96,…,0.2×1.04}, jitter magnitudes 1e−3 and 2e−3}) that broadens basins. Future designs should reuse the LP bootstrap and uniform inflation + LP whenever centers are plausible and boundaries are not tight, maintain Σ r acceptance gates to keep improvements monotone, and diversify hex-staggered seeds with micro-restarts at n = 21.
- LP-Preselect TriHex+ with Extra Seeds and Late-Stage Polish: Use LP-only preselection to run a fixed-center LP on 6 jittered seeds per (pattern, spacing), keep the top 2 by Σr_LP, and skip the barrier on the rest, leveraging an exact proxy strongly correlated with final Σr to focus compute on promising basins; augment seed patterns with [5,5,5,3,3] and [6,4,4,4,3] at spacings around 0.2 and apply boundary-aware jitter to broaden reachable contact graphs for n=21; and, during the barrier schedule, when the count of near-active pairwise constraints s_ij < 2e−4 increases over the prior μ-stage, boost iterations for the last two μ values by 20% to settle tight tangencies without destabilizing earlier phases—an approach that achieved sum_radii 2.3411715429651374 with validity 1.0, slightly exceeding the parent score 2.3411715170340597 under strict feasibility gates.
- LP-Preselect HexTrust with Uniform Inflation and LP–Mini-Barrier Polish: The algorithm used an LP-only preselection over jittered hex-lattice seeds, keeping the top-K survivors per (pattern, spacing) by a single fixed-center LP sum of radii, to focus subsequent optimization on high-potential basins.
- LP-Preselect HexTrust with Uniform Inflation and LP–Mini-Barrier Polish: It performed uniform scaling of centers and radii about (0.5, 0.5) until a boundary constraint became tight, then ran a fixed-center LP and accepted the change only when the sum of radii increased, safely converting boundary slack without creating overlaps.
- LP-Preselect HexTrust with Uniform Inflation and LP–Mini-Barrier Polish: A late LP–mini-barrier–LP sandwich—LP inflation, a short interior-barrier ascent with a small μ schedule and backtracking that maintained all slacks strictly positive, then another LP—tightened near-tangencies and immediately reallocated created slack, with acceptance gated by sum-of-radii improvement.
- LP-Preselect HexTrust with Uniform Inflation and LP–Mini-Barrier Polish: Monotone acceptance gates were enforced after periodic LPs, uniform inflation, and sandwich phases, committing updates only if the sum of radii strictly increased and finishing with a tiny global shrink plus an overlap check to ensure strict feasibility.
- LP-Preselect HexTrust with Uniform Inflation and LP–Mini-Barrier Polish: In this event the method achieved sum_radii 2.3479887770194026 with validity 1.0, exceeding the parent baselines 2.307922524313782 and 2.305402731101652, evidencing the effectiveness of LP preselection, uniform inflation with follow-up LP, and the LP–mini-barrier–LP polish under unit-square constraints.
- LP-Preselect TriHex++ with Dual Uniform Inflation and LP–Barrier–LP Polish: Reducing the perimeter ≤ 4 constraint to packing strictly inside the unit square lets the optimizer focus solely on maximizing Σr under non-overlap, and pairing multi-start true-hexagonal seeding with LP-only fixed-center preselection provides high-density starts. Interleaving exact fixed-center LP inflations with gentle center moves and dual uniform scaling about (0.5, 0.5), all under monotone acceptance gates, reliably converts boundary and pairwise slack into larger radii while staying strictly feasible, as evidenced by validity 1.0 and Σr = 2.264166456721675 on n=21 (well above the ~2.10 grid baseline and consistent with prior 2.34–2.35 reports). For future designs under similar box-like constraints, reuse this sequence: hex-lattice multi-start with LP preselection, periodic fixed-center LP inflations during growth-and-move, one or two global uniform inflations each followed by LP, and an LP–mini-barrier–LP polish with a tiny final shrink to cement strict feasibility.
- Endgame LP–Mini-Barrier–LP Sandwich with Annealed-ε Gate: The method adds a final LP warm start, a short interior-barrier ascent on (x, y, r) with fixed W, H and lazy constraint augmentation, and a follow-up fixed-center LP, each substep committing only if the sum of radii increases strictly.
- Mini-barrier polish in the endgame sandwich: The barrier stage runs μ ∈ [2e−3, 1e−3, 5e−4] with 40–60 iterations per μ, backtracking line search, base_step ≈ 0.02, and anneals the barrier’s inner clearance from ε_b0 = min(eps_stage, 1e−8) to ε_b1 = 1e−12, after multiplying radii by 0.999999 to ensure strict slacks for log-barrier stability.
- Final LP settings in the endgame sandwich: The fixed-center LPs use eps_lp_final = min(eps_stage, 1e−10) for the warm start and eps_lp_final = 1e−12 for the final capture to recover slack near tangencies while maintaining boundary containment and non-overlap.
- Generation 9 performance for Endgame LP–Mini-Barrier–LP Sandwich with Annealed-ε Gate: This run achieved sum_radii = 2.3393700908063253 with validity = 1.0, exceeding the parent score 2.339366090823248, consistent with small monotone improvements from endgame slack reallocation at N = 21.
- LP-Preselect Multi-Start with LP-Warm Starts: In the N=21 circle-packing task with rectangle perimeter ≤ 4, the method ran a fixed-center LP on each multi-start seed (when SciPy was available) using eps_lp_preselect = min(current_eps, 1e-10), ranked seeds by Σr_lp, and kept only the top K = min(2, number_of_seeds) for the expensive polishing; it modestly increased multistart_k from 5 to 8 so the cheap LP could evaluate more seeds, and it passed the LP radii forward as warm starts into polishing and the final LP–mini-barrier–LP sandwich, which removes structured slack to reduce barrier/solver iterations and focuses compute on high-potential basins; in Generation 11 this configuration achieved sum_radii 2.3394360632738476 with validity 1.0, slightly improving on its parent’s 2.3393700908063253 under strict feasibility, and prior variants consistently showed +0.002 to +0.008 gains in Σr at N = 21.
- Adaptive LP-Cadence with 5-Scale Seeding and Stronger LP Preselect: This variant succeeded by combining five-scale hex-lattice seeding with a slightly larger LP-only survivor pool (jitter_count 8, top_K 3, eps_lp = 1e−10 with LP radii passed as warm starts) to broaden basin coverage at N=21, and by making the fixed-center LP correction cadence adaptive to constraint tightening: in each trust-region block, reduce K to max(20, floor(0.6·K)) when Σr fails to improve or near-active pairs increase by ≥10%, otherwise relax to min(60, ceil(1.1·K)); LP updates are committed only when Σr strictly increases. Applied to packing 21 disjoint circles within the unit square (which keeps the circumscribing rectangle perimeter ≤ 4), this design achieved sum_radii = 2.341128858724163 with validity = 1.0, and prior runs indicated the adaptive LP cadence near tangencies can add +0.001 to +0.004 in Σr at n=21; future designs should reuse this adaptive LP cadence and multi-scale LP preselection when configurations approach tangency to convert emerging slack into radii promptly without unnecessary LP calls.
- Adaptive LP-Cadence in Trust-Region Growth: Within the trust-region growth-and-move loop, a fixed "call LP every K=50 iterations" schedule is replaced by a blockwise controller that adapts LP call frequency based on near-active constraint signals evaluated at the end of each block of 50 iterations.
- Near-active constraint indicators S_pair and S_wall: The controller tracks S_pair = |{(i,j): d_ij − (r_i + r_j) < τ}| and S_wall = |{i: min(x_i − r_i, 1 − x_i − r_i, y_i − r_i, 1 − y_i − r_i) < τ}| with τ ≈ 0.5·near_thresh (e.g., τ = 0.015 when near_thresh = 0.03).
- LP period K update rule: After each block, if Σr failed to improve in that block or S_pair + S_wall increased by at least 10% versus the previous block, the LP period is shrunk via K ← max(20, floor(0.6·K)); otherwise it is relaxed via K ← min(60, ceil(1.1·K)).
- Monotone acceptance gate: The algorithm adopts LP-reallocated radii only when Σr strictly increases and then immediately reprojects and polishes as before.
- Initialization and state tracking: The controller initializes K to 50, stores last_S = S_pair + S_wall, and updates last_S after each block.
- Rationale for adaptive LP cadence: When many constraints tighten rapidly, calling the fixed-center LP sooner converts emerging slack into Σr before local growth/move steps stall, whereas when the system is not tightening, fewer LP calls save time without hurting progress.
- Prior evidence on similar configurations: This adaptive cadence is reported to yield consistent +0.001 to +0.004 gains in Σr at n = 21 with negligible overhead.
- Integration scope: The change is localized to the trust-region routine where periodic LP is invoked by adding the counters and the blockwise K update, while seeding, preselection LP, uniform inflations, barrier polish, and final LP stages remain unchanged.
- This run (Generation 5): The run achieved sum_radii = 2.3494688586580548 with validity = 1.0 and a score of 2.3494688586580548, compared to the parent score 2.335503422834669, with no errors reported.

```python
#!/usr/bin/env python3
"""SOCP-inspired packing of 21 circles inside a unit square (perimeter 4).

We construct exactly 21 disjoint circles, each fully contained in [0,1]x[0,1],
so the minimal axis-aligned circumscribing rectangle has perimeter exactly 4.
We maximize the sum of radii approximately via a smooth interior-barrier ascent.

Core idea:
- Fix the frame to a unit square, ensuring perimeter 4.
- Variables: centers (x_i, y_i) and radii r_i >= 0 for i=1..21.
- Constraints:
  * Non-overlap: ||(xi - xj, yi - yj)|| >= r_i + r_j + eps
  * In-box: r_i + eps <= x_i <= 1 - r_i - eps and same for y
  * Positivity: r_i > 0
- Objective: maximize sum(r_i).

We implement a smooth barrier objective:
  f(x,y,r) = sum(r_i) + mu * (sum over constraints of log(slack))
with slacks s>0 for all constraints:
  s_ij = ||d_ij|| - (r_i + r_j + eps)
  s_xL = x_i - r_i - eps
  s_xR = 1 - r_i - eps - x_i
  s_yB = y_i - r_i - eps
  s_yT = 1 - r_i - eps - y_i
  s_r  = r_i

We then run a gradient-ascent with backtracking line search for decreasing mu.

This is not a full SOCP solver; it's a robust, projected barrier ascent suitable
for this small instance. It produces a feasible packing and typically improves
over a uniform 5x5 grid baseline by reallocating radii/positions.
"""

import json
import math
from typing import List, Tuple, Optional

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 21) -> np.ndarray:
    """Construct 21 disjoint circles inside a unit square maximizing sum radii.

    This implements the combined "LP–mini-barrier–LP with diversified hex seeding"
    algorithm described in the task notes.

    Returns:
        numpy array of shape (num_circles, 3) with rows [x, y, r].
    """
    if num_circles != 21:
        # Fallback to a safe uniform tiny packing if asked for other sizes
        n = num_circles
        grid = int(math.ceil(math.sqrt(n)))
        # Evenly space in a grid, tiny radii to ensure disjointness and containment
        r0 = 0.01
        xs = np.linspace(r0 + 1e-4, 1 - r0 - 1e-4, grid)
        ys = np.linspace(r0 + 1e-4, 1 - r0 - 1e-4, grid)
        pts = []
        for yy in ys:
            for xx in xs:
                pts.append([xx, yy])
                if len(pts) == n:
                    break
            if len(pts) == n:
                break
        centers = np.array(pts, dtype=float)
        radii = np.full(n, r0, dtype=float)
        return np.column_stack((centers, radii))

    # Parameters
    N = num_circles
    # Strict internal separation epsilon for all slacks
    eps = 1e-6
    tiny = 1e-12

    # Precompute all unordered pairs i<j for pairwise constraints
    pair_i = []
    pair_j = []
    for i in range(N):
        for j in range(i + 1, N):
            pair_i.append(i)
            pair_j.append(j)
    pair_i = np.array(pair_i, dtype=int)
    pair_j = np.array(pair_j, dtype=int)

    # Exact LP inflation of radii for fixed centers (optional if SciPy is present).
    # Solve:
    #   maximize sum(r_i)
    #   subject to 0 <= r_i <= m_i (m_i = min(x_i, 1-x_i, y_i, 1-y_i))
    #              r_i + r_j <= d_ij - eps  for all pairs i<j
    def lp_inflate(centers_xy: np.ndarray, eps_lp: float) -> Optional[np.ndarray]:
        try:
            from scipy.optimize import linprog  # type: ignore
        except Exception:
            return None

        n = centers_xy.shape[0]
        x_c = centers_xy[:, 0]
        y_c = centers_xy[:, 1]
        # Boundary caps m_i based on fixed centers
        m = np.minimum.reduce([x_c, 1.0 - x_c, y_c, 1.0 - y_c])

        # Build pairwise constraints A_ub r <= b_ub (r_i + r_j <= d_ij - eps)
        num_pairs = n * (n - 1) // 2
        A_ub = np.zeros((num_pairs, n), dtype=float)
        b_ub = np.zeros(num_pairs, dtype=float)

        idx = 0
        for i in range(n):
            xi = x_c[i]
            yi = y_c[i]
            for j in range(i + 1, n):
                dx = xi - x_c[j]
                dy = yi - y_c[j]
                dij = math.hypot(dx, dy)
                rhs = max(dij - eps_lp, 0.0)  # be defensive
                A_ub[idx, i] = 1.0
                A_ub[idx, j] = 1.0
                b_ub[idx] = rhs
                idx += 1

        # Objective: maximize sum r -> minimize -sum r
        c = -np.ones(n, dtype=float)
        bounds = [(0.0, float(mi)) for mi in m]

        res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        if not res.success or res.x is None:
            return None
        r_sol = np.asarray(res.x, dtype=float)
        r_sol = np.maximum(r_sol, 0.0)
        return r_sol

    # Hex-like seed generator with true triangular-lattice staggering:
    # - pattern is a list of 5 row counts summing to 21, e.g., [5,4,4,4,4]
    # - horizontal spacing s; vertical spacing v = s*sqrt(3)/2
    # - rows centered vertically around 0.5 with y_k = 0.5 + (k-2)*v for k=0..4
    # - horizontally, alternate rows are shifted by a half-step:
    #     row_shift = ((k % 2) - 0.5) * 0.5 * s  -> {-0.25*s, +0.25*s, ...}
    #   and positions are:
    #     x = 0.5 + row_shift + (m - (n_k - 1)/2) * s,  m=0..n_k-1
    # The base seed is unjittered; jitter will be applied outside this function.
    def hex_seed(pattern: List[int], s: float) -> np.ndarray:
        v = s * (math.sqrt(3.0) / 2.0)
        centers: List[Tuple[float, float]] = []
        for k, n_k in enumerate(pattern):
            yk = 0.5 + (k - 2) * v
            row_shift = ((k % 2) - 0.5) * 0.5 * s
            for m in range(n_k):
                xk = 0.5 + row_shift + (m - (n_k - 1) / 2.0) * s
                centers.append((xk, yk))
        centers_arr = np.array(centers, dtype=float)
        # Clip into the unit square interior (leaving a tiny margin for jitter room)
        centers_arr = np.clip(centers_arr, 1e-3, 1 - 1e-3)
        return centers_arr

    # Per-seed pipeline: LP bootstrap → barrier ascent (with periodic LP) → LP → uniform inflation #1 → LP
    def run_barrier_from_seed(centers_init: np.ndarray) -> np.ndarray:
        # Initialize small radii to ensure a strictly feasible start
        r0 = 0.01
        x = centers_init[:, 0].copy()
        y = centers_init[:, 1].copy()
        r = np.full(N, r0, dtype=float)

        # Barrier ascent hyperparameters
        mu_schedule = [5e-2, 2e-2, 1e-2, 5e-3, 3e-3, 1e-3]
        iters_per_mu = 300
        base_step = 0.05
        min_step = 1e-8
        backtrack = 0.5

        def compute_slacks(xv, yv, rv):
            # Pairwise distances
            dx = xv[pair_i] - xv[pair_j]
            dy = yv[pair_i] - yv[pair_j]
            d = np.hypot(dx, dy)
            d = np.maximum(d, tiny)
            s_pairs = d - (rv[pair_i] + rv[pair_j] + eps)

            # Boundary slacks
            s_xL = xv - rv - eps
            s_xR = 1.0 - rv - eps - xv
            s_yB = yv - rv - eps
            s_yT = 1.0 - rv - eps - yv
            # Positive radii
            s_r = rv

            return (dx, dy, d, s_pairs, s_xL, s_xR, s_yB, s_yT, s_r)

        def feasible(slacks) -> bool:
            # All slacks must be strictly positive for barrier
            for arr in slacks[3:]:
                if np.any(arr <= 0.0):
                    return False
            return True

        def objective(mu, slacks, rv) -> float:
            # f = sum r + mu * sum log(slacks)
            _, _, _, s_pairs, s_xL, s_xR, s_yB, s_yT, s_r = slacks
            logs = (
                np.sum(np.log(s_pairs))
                + np.sum(np.log(s_xL))
                + np.sum(np.log(s_xR))
                + np.sum(np.log(s_yB))
                + np.sum(np.log(s_yT))
                + np.sum(np.log(s_r))
            )
            return float(np.sum(rv)) + mu * float(logs)

        # Ensure initial feasibility; if not, shrink radii and recenter slightly
        slacks = compute_slacks(x, y, r)
        if not feasible(slacks):
            r = np.full(N, 0.005, dtype=float)
            slacks = compute_slacks(x, y, r)
            if not feasible(slacks):
                x = 0.5 + 0.8 * (x - 0.5)
                y = 0.5 + 0.8 * (y - 0.5)
                r = np.full(N, 0.003, dtype=float)
                slacks = compute_slacks(x, y, r)

        # LP bootstrap (fixed centers) to accelerate radii allocation
        centers_now = np.column_stack((x, y))
        r_lp0 = lp_inflate(centers_now, eps)
        if r_lp0 is not None and np.sum(r_lp0) > np.sum(r) + tiny:
            r = r_lp0
            # Tiny uniform shrink to restore strict inequalities, then re-clip centers
            r *= 0.999
            x = np.clip(x, r + eps, 1.0 - r - eps)
            y = np.clip(y, r + eps, 1.0 - r - eps)
            slacks = compute_slacks(x, y, r)
            if not feasible(slacks):
                # If any slack is too tight numerically, shrink a hair more
                r *= 0.999
                x = np.clip(x, r + eps, 1.0 - r - eps)
                y = np.clip(y, r + eps, 1.0 - r - eps)
                slacks = compute_slacks(x, y, r)

        # Barrier ascent with periodic LP inflations after each mu-stage
        for mu in mu_schedule:
            for _ in range(iters_per_mu):
                dx_, dy_, d_, s_pairs, s_xL, s_xR, s_yB, s_yT, s_r = slacks

                # Gradients initialization
                grad_x = np.zeros(N, dtype=float)
                grad_y = np.zeros(N, dtype=float)
                grad_r = np.ones(N, dtype=float)  # from sum(r_i)

                # Pairwise constraints gradient
                coef = mu / (s_pairs * d_)
                np.add.at(grad_x, pair_i, coef * dx_)
                np.add.at(grad_x, pair_j, -coef * dx_)
                np.add.at(grad_y, pair_i, coef * dy_)
                np.add.at(grad_y, pair_j, -coef * dy_)
                coef_r = mu / s_pairs
                np.add.at(grad_r, pair_i, -coef_r)
                np.add.at(grad_r, pair_j, -coef_r)

                # Boundary constraints gradients via slacks
                grad_x += mu / s_xL
                grad_r += -mu / s_xL
                grad_x += -mu / s_xR
                grad_r += -mu / s_xR
                grad_y += mu / s_yB
                grad_r += -mu / s_yB
                grad_y += -mu / s_yT
                grad_r += -mu / s_yT
                # Positivity: s_r = r
                grad_r += mu / s_r

                # Backtracking line search for feasibility and ascent
                step = base_step
                f_curr = objective(mu, slacks, r)
                improved = False
                for _bt in range(40):
                    xn = x + step * grad_x
                    yn = y + step * grad_y
                    rn = r + step * grad_r
                    # Compute new slacks
                    slacks_n = compute_slacks(xn, yn, rn)
                    if feasible(slacks_n):
                        f_new = objective(mu, slacks_n, rn)
                        if f_new >= f_curr:
                            x, y, r = xn, yn, rn
                            slacks = slacks_n
                            improved = True
                            break
                    step *= backtrack
                    if step < min_step:
                        break
                if not improved:
                    base_step *= 0.9
                    if base_step < min_step:
                        break

            # Periodic fixed-center LP inflation after barrier stage (accept only if Σr increases)
            centers_now = np.column_stack((x, y))
            r_lp = lp_inflate(centers_now, eps)
            if r_lp is not None and np.sum(r_lp) > np.sum(r) + tiny:
                r = r_lp
                # Recompute slacks with updated radii; ensure strict feasibility defensively
                slacks = compute_slacks(x, y, r)
                if not feasible(slacks):
                    r *= 0.999
                    slacks = compute_slacks(x, y, r)

        # Final fixed-center LP inflation before uniform scaling
        centers_now = np.column_stack((x, y))
        r_lp = lp_inflate(centers_now, eps)
        if r_lp is not None and np.sum(r_lp) > np.sum(r) + tiny:
            r = r_lp

        # Global uniform inflation about square center (0.5, 0.5)
        def global_uniform_inflation(xv, yv, rv, eps_g):
            # Constraints for each circle i (linearized in t):
            # s_xL' = 0.5 - eps + t*(x - 0.5 - r) >= 0
            # s_xR' = 0.5 - eps - t*(x - 0.5 + r) >= 0
            # s_yB' = 0.5 - eps + t*(y - 0.5 - r) >= 0
            # s_yT' = 0.5 - eps - t*(y - 0.5 + r) >= 0
            A = 0.5 - eps_g
            B_xL = xv - 0.5 - rv
            B_xR = -(xv - 0.5 + rv)
            B_yB = yv - 0.5 - rv
            B_yT = -(yv - 0.5 + rv)

            t_ubs = []

            def append_bounds(B_arr):
                # For B < 0, the inequality imposes t <= -A/B
                mask = B_arr < 0
                if np.any(mask):
                    t_ubs.extend(list((-A / B_arr[mask])))

            append_bounds(B_xL)
            append_bounds(B_xR)
            append_bounds(B_yB)
            append_bounds(B_yT)

            if len(t_ubs) == 0:
                # No active bounds -> scaling wouldn't change
                return xv, yv, rv

            t_max = min(t_ubs)
            if t_max > 1.0 + 1e-12:
                t = t_max * (1.0 - 1e-12)
                xv2 = 0.5 + t * (xv - 0.5)
                yv2 = 0.5 + t * (yv - 0.5)
                rv2 = t * rv
                return xv2, yv2, rv2
            else:
                return xv, yv, rv

        # Acceptance-gated uniform inflation #1 + LP
        sum_before = float(np.sum(r))
        x_old, y_old, r_old = x.copy(), y.copy(), r.copy()
        x_inf, y_inf, r_inf = global_uniform_inflation(x, y, r, eps)
        centers_now = np.column_stack((x_inf, y_inf))
        r_lp = lp_inflate(centers_now, eps)
        if r_lp is not None and np.sum(r_lp) > np.sum(r_inf) + tiny:
            r_inf = r_lp
        if float(np.sum(r_inf)) > sum_before + tiny:
            x, y, r = x_inf, y_inf, r_inf
        else:
            x, y, r = x_old, y_old, r_old

        # Final polishing: small projection to ensure strict feasibility
        r = np.maximum(r, 1e-6)
        x = np.clip(x, r + eps, 1.0 - r - eps)
        y = np.clip(y, r + eps, 1.0 - r - eps)

        # Pairwise safety shrink passes to remove any tiny overlap violations
        for _ in range(3):
            dxp = x[pair_i] - x[pair_j]
            dyp = y[pair_i] - y[pair_j]
            d = np.hypot(dxp, dyp)
            need = r[pair_i] + r[pair_j] + eps
            viol = need - d
            if np.all(viol <= 0):
                break
            for k in range(len(pair_i)):
                if viol[k] > 0:
                    i = pair_i[k]; j = pair_j[k]
                    if r[i] >= r[j]:
                        r[i] = max(r[i] - 0.5 * viol[k], 1e-6)
                    else:
                        r[j] = max(r[j] - 0.5 * viol[k], 1e-6)
            x = np.clip(x, r + eps, 1.0 - r - eps)
            y = np.clip(y, r + eps, 1.0 - r - eps)

        return np.column_stack((x, y, r))

    # Multi-start hex seeding with diversification:
    # Patterns expanded to broaden contact graphs
    patterns = [
        [5, 4, 4, 4, 4],
        [4, 5, 4, 4, 4],
        [4, 4, 5, 4, 4],
        [4, 4, 4, 5, 4],
        [5, 4, 5, 4, 3],
        [5, 5, 4, 4, 3],
        [4, 5, 5, 4, 3],
    ]
    # Lattice spacings around 0.2 with broader variation
    spacings = [0.2 * 0.96, 0.2 * 0.98, 0.2, 0.2 * 1.02, 0.2 * 1.04]

    best_circles = None
    best_sum = -1.0

    rng_global = np.random.default_rng()

    for pat in patterns:
        for s in spacings:
            # Base seed with true hex staggering and no jitter
            centers_base = hex_seed(pat, s)

            # Three jittered variants per (pattern, spacing):
            # - two with magnitude 1e-3
            # - one with magnitude 2e-3
            seeds = []
            for _ in range(2):
                jitter = rng_global.uniform(-1e-3, 1e-3, size=centers_base.shape)
                centers_j = np.clip(centers_base + jitter, 1e-3, 1 - 1e-3)
                seeds.append(centers_j)
            jitter = rng_global.uniform(-2e-3, 2e-3, size=centers_base.shape)
            centers_j = np.clip(centers_base + jitter, 1e-3, 1 - 1e-3)
            seeds.append(centers_j)

            # Run the pipeline for each jittered seed and keep the best by sum of radii
            local_best = None
            local_best_sum = -1.0
            for seed in seeds:
                circles = run_barrier_from_seed(seed)
                sum_r = float(np.sum(circles[:, 2]))
                if sum_r > local_best_sum + tiny:
                    local_best_sum = sum_r
                    local_best = circles

            if local_best is not None and local_best_sum > best_sum + tiny:
                best_sum = local_best_sum
                best_circles = local_best

    assert best_circles is not None

    # LP–mini-barrier–LP sandwich refinement on the best solution across seeds.
    # Accept only if Σr increases, with optional uniform inflation #2 + LP.
    def lp_mini_barrier_lp_refine(circles_in: np.ndarray) -> np.ndarray:
        x = circles_in[:, 0].copy()
        y = circles_in[:, 1].copy()
        r = circles_in[:, 2].copy()

        sum_orig = float(np.sum(r))
        best_x, best_y, best_r = x.copy(), y.copy(), r.copy()
        best_sum = sum_orig

        # Utility subroutines (same structure as in main barrier)
        def compute_slacks(xv, yv, rv):
            dx = xv[pair_i] - xv[pair_j]
            dy = yv[pair_i] - yv[pair_j]
            d = np.hypot(dx, dy)
            d = np.maximum(d, tiny)
            s_pairs = d - (rv[pair_i] + rv[pair_j] + eps)
            s_xL = xv - rv - eps
            s_xR = 1.0 - rv - eps - xv
            s_yB = yv - rv - eps
            s_yT = 1.0 - rv - eps - yv
            s_r = rv
            return (dx, dy, d, s_pairs, s_xL, s_xR, s_yB, s_yT, s_r)

        def feasible(slacks) -> bool:
            for arr in slacks[3:]:
                if np.any(arr <= 0.0):
                    return False
            return True

        # LP1 with frozen centers
        centers_now = np.column_stack((x, y))
        r_lp = lp_inflate(centers_now, eps)
        if r_lp is not None and np.sum(r_lp) > np.sum(r) + tiny:
            r = r_lp
            # Update best with LP1 if improved
            if float(np.sum(r)) > best_sum + tiny:
                best_x, best_y, best_r = x.copy(), y.copy(), r.copy()
                best_sum = float(np.sum(r))

        # Slight shrink to ensure strict feasibility for barrier (avoid log(0))
        r *= 0.999999
        slacks = compute_slacks(x, y, r)
        if not feasible(slacks):
            r *= 0.999
            slacks = compute_slacks(x, y, r)

        # Mini-barrier polish: short ascent with smaller step sizes
        mu_schedule = [2e-3, 1e-3, 5e-4]
        iters_per_mu = 60
        base_step = 0.02
        min_step = 1e-8
        backtrack = 0.5

        for mu in mu_schedule:
            for _ in range(iters_per_mu):
                dx_, dy_, d_, s_pairs, s_xL, s_xR, s_yB, s_yT, s_r = slacks

                grad_x = np.zeros(N, dtype=float)
                grad_y = np.zeros(N, dtype=float)
                grad_r = np.ones(N, dtype=float)

                coef = mu / (s_pairs * d_)
                np.add.at(grad_x, pair_i, coef * dx_)
                np.add.at(grad_x, pair_j, -coef * dx_)
                np.add.at(grad_y, pair_i, coef * dy_)
                np.add.at(grad_y, pair_j, -coef * dy_)
                coef_r = mu / s_pairs
                np.add.at(grad_r, pair_i, -coef_r)
                np.add.at(grad_r, pair_j, -coef_r)

                grad_x += mu / s_xL
                grad_r += -mu / s_xL
                grad_x += -mu / s_xR
                grad_r += -mu / s_xR
                grad_y += mu / s_yB
                grad_r += -mu / s_yB
                grad_y += -mu / s_yT
                grad_r += -mu / s_yT
                grad_r += mu / s_r

                # Current barrier objective for comparison
                logs = (
                    np.sum(np.log(s_pairs))
                    + np.sum(np.log(s_xL))
                    + np.sum(np.log(s_xR))
                    + np.sum(np.log(s_yB))
                    + np.sum(np.log(s_yT))
                    + np.sum(np.log(s_r))
                )
                f_curr = float(np.sum(r)) + mu * float(logs)

                step = base_step
                improved = False
                for _bt in range(40):
                    xn = x + step * grad_x
                    yn = y + step * grad_y
                    rn = r + step * grad_r
                    slacks_n = compute_slacks(xn, yn, rn)
                    if feasible(slacks_n):
                        logs_n = (
                            np.sum(np.log(slacks_n[3]))
                            + np.sum(np.log(slacks_n[4]))
                            + np.sum(np.log(slacks_n[5]))
                            + np.sum(np.log(slacks_n[6]))
                            + np.sum(np.log(slacks_n[7]))
                            + np.sum(np.log(slacks_n[8]))
                        )
                        f_new = float(np.sum(rn)) + mu * float(logs_n)
                        if f_new >= f_curr:
                            x, y, r = xn, yn, rn
                            slacks = slacks_n
                            improved = True
                            break
                    step *= backtrack
                    if step < min_step:
                        break
                if not improved:
                    base_step *= 0.9
                    if base_step < min_step:
                        break

        # LP2 with frozen centers; keep if improves best_sum
        centers_now = np.column_stack((x, y))
        r_lp2 = lp_inflate(centers_now, eps)
        if r_lp2 is not None and np.sum(r_lp2) > np.sum(r) + tiny:
            r = r_lp2
        if float(np.sum(r)) > best_sum + tiny:
            best_x, best_y, best_r = x.copy(), y.copy(), r.copy()
            best_sum = float(np.sum(r))

        # Optional uniform inflation #2 + LP; accept only if Σr increases
        def global_uniform_inflation(xv, yv, rv, eps_g):
            A = 0.5 - eps_g
            B_xL = xv - 0.5 - rv
            B_xR = -(xv - 0.5 + rv)
            B_yB = yv - 0.5 - rv
            B_yT = -(yv - 0.5 + rv)

            t_ubs = []

            def append_bounds(B_arr):
                mask = B_arr < 0
                if np.any(mask):
                    t_ubs.extend(list((-A / B_arr[mask])))

            append_bounds(B_xL)
            append_bounds(B_xR)
            append_bounds(B_yB)
            append_bounds(B_yT)

            if len(t_ubs) == 0:
                return xv, yv, rv

            t_max = min(t_ubs)
            if t_max > 1.0 + 1e-12:
                t = t_max * (1.0 - 1e-12)
                xv2 = 0.5 + t * (xv - 0.5)
                yv2 = 0.5 + t * (yv - 0.5)
                rv2 = t * rv
                return xv2, yv2, rv2
            else:
                return xv, yv, rv

        x2, y2, r2 = global_uniform_inflation(best_x, best_y, best_r, eps)
        centers_now2 = np.column_stack((x2, y2))
        r_lp3 = lp_inflate(centers_now2, eps)
        if r_lp3 is not None and np.sum(r_lp3) > np.sum(r2) + tiny:
            r2 = r_lp3
        if float(np.sum(r2)) > best_sum + tiny:
            best_x, best_y, best_r = x2, y2, r2
            best_sum = float(np.sum(best_r))

        # Final safety projection and overlap polish (strict feasibility)
        best_r = np.maximum(best_r, 1e-6)
        best_x = np.clip(best_x, best_r + eps, 1.0 - best_r - eps)
        best_y = np.clip(best_y, best_r + eps, 1.0 - best_r - eps)
        for _ in range(3):
            dxp = best_x[pair_i] - best_x[pair_j]
            dyp = best_y[pair_i] - best_y[pair_j]
            d = np.hypot(dxp, dyp)
            need = best_r[pair_i] + best_r[pair_j] + eps
            viol = need - d
            if np.all(viol <= 0):
                break
            for k in range(len(pair_i)):
                if viol[k] > 0:
                    i = pair_i[k]; j = pair_j[k]
                    if best_r[i] >= best_r[j]:
                        best_r[i] = max(best_r[i] - 0.5 * viol[k], 1e-6)
                    else:
                        best_r[j] = max(best_r[j] - 0.5 * viol[k], 1e-6)
            best_x = np.clip(best_x, best_r + eps, 1.0 - best_r - eps)
            best_y = np.clip(best_y, best_r + eps, 1.0 - best_r - eps)

        # Return improved solution if any; otherwise the input
        if best_sum > sum_orig + tiny:
            return np.column_stack((best_x, best_y, best_r))
        else:
            return circles_in

    # Apply refinement and accept only if Σr increases (guard inside function already ensures monotonicity).
    refined = lp_mini_barrier_lp_refine(best_circles)
    if np.sum(refined[:, 2]) > np.sum(best_circles[:, 2]) + tiny:
        best_circles = refined

    return best_circles
# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```

```python
#!/usr/bin/env python3
"""SOCP-inspired packing of 21 circles inside a unit square (perimeter 4).

We construct exactly 21 disjoint circles, each fully contained in [0,1]x[0,1],
so the minimal axis-aligned circumscribing rectangle has perimeter exactly 4.
We maximize the sum of radii approximately via a smooth interior-barrier ascent.

Core idea:
- Fix the frame to a unit square, ensuring perimeter 4.
- Variables: centers (x_i, y_i) and radii r_i >= 0 for i=1..21.
- Constraints:
  * Non-overlap: ||(xi - xj, yi - yj)|| >= r_i + r_j + eps
  * In-box: r_i + eps <= x_i <= 1 - r_i - eps and same for y
  * Positivity: r_i > 0
- Objective: maximize sum(r_i).

We implement a smooth barrier objective:
  f(x,y,r) = sum(r_i) + mu * (sum over constraints of log(slack))
with slacks s>0 for all constraints:
  s_ij = ||d_ij|| - (r_i + r_j + eps)
  s_xL = x_i - r_i - eps
  s_xR = 1 - r_i - eps - x_i
  s_yB = y_i - r_i - eps
  s_yT = 1 - r_i - eps - y_i
  s_r  = r_i

We then run a gradient-ascent with backtracking line search for decreasing mu.

This is not a full SOCP solver; it's a robust, projected barrier ascent suitable
for this small instance. It produces a feasible packing and typically improves
over a uniform 5x5 grid baseline by reallocating radii/positions.
"""

import json
import math
from typing import List, Tuple, Optional

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 21) -> np.ndarray:
    """Construct 21 disjoint circles inside a unit square maximizing sum radii.

    Algorithm = TriHex+ pipeline with targeted mutations:
      - Hex-staggered seeding over multiple row-count patterns and spacings
      - LP-only preselection: 6 jitters → run fixed-center LP to score Σr, keep top-2
      - Full barrier ascent with periodic LP on survivors
      - Global uniform inflation + LP (accept-only-if-improves)
      - Best-of-seeds selection
      - LP–mini-barrier–LP sandwich refinement
      - Safety polish

    Returns:
        numpy array of shape (num_circles, 3) with rows [x, y, r].
    """
    if num_circles != 21:
        # Fallback to a safe uniform tiny packing if asked for other sizes
        n = num_circles
        grid = int(math.ceil(math.sqrt(n)))
        r0 = 0.01
        xs = np.linspace(r0 + 1e-4, 1 - r0 - 1e-4, grid)
        ys = np.linspace(r0 + 1e-4, 1 - r0 - 1e-4, grid)
        pts = []
        for yy in ys:
            for xx in xs:
                pts.append([xx, yy])
                if len(pts) == n:
                    break
            if len(pts) == n:
                break
        centers = np.array(pts, dtype=float)
        radii = np.full(n, r0, dtype=float)
        return np.column_stack((centers, radii))

    # Parameters
    N = num_circles
    # Strict internal separation epsilon for all slacks
    eps = 1e-6
    tiny = 1e-12

    # Precompute all unordered pairs i<j for pairwise constraints
    pair_i = []
    pair_j = []
    for i in range(N):
        for j in range(i + 1, N):
            pair_i.append(i)
            pair_j.append(j)
    pair_i = np.array(pair_i, dtype=int)
    pair_j = np.array(pair_j, dtype=int)

    # Exact LP inflation of radii for fixed centers.
    # Solve:
    #   maximize sum(r_i)
    #   subject to 0 <= r_i <= m_i (m_i = min(x_i, 1-x_i, y_i, 1-y_i))
    #              r_i + r_j <= d_ij - eps  for all pairs i<j
    def lp_inflate(centers_xy: np.ndarray, eps_lp: float) -> Optional[np.ndarray]:
        try:
            from scipy.optimize import linprog  # type: ignore
        except Exception:
            return None

        n = centers_xy.shape[0]
        x_c = centers_xy[:, 0]
        y_c = centers_xy[:, 1]
        # Boundary caps m_i based on fixed centers
        m = np.minimum.reduce([x_c, 1.0 - x_c, y_c, 1.0 - y_c])

        # Build pairwise constraints A_ub r <= b_ub (r_i + r_j <= d_ij - eps)
        num_pairs = n * (n - 1) // 2
        A_ub = np.zeros((num_pairs, n), dtype=float)
        b_ub = np.zeros(num_pairs, dtype=float)

        idx = 0
        for i in range(n):
            xi = x_c[i]
            yi = y_c[i]
            for j in range(i + 1, n):
                dx = xi - x_c[j]
                dy = yi - y_c[j]
                dij = math.hypot(dx, dy)
                rhs = max(dij - eps_lp, 0.0)  # be defensive
                A_ub[idx, i] = 1.0
                A_ub[idx, j] = 1.0
                b_ub[idx] = rhs
                idx += 1

        # Objective: maximize sum r -> minimize -sum r
        c = -np.ones(n, dtype=float)
        bounds = [(0.0, float(mi)) for mi in m]

        res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        if not res.success or res.x is None:
            return None
        r_sol = np.asarray(res.x, dtype=float)
        r_sol = np.maximum(r_sol, 0.0)
        return r_sol

    # Hex-like seed generator with true triangular-lattice staggering:
    # - pattern is a list of 5 row counts summing to 21, e.g., [5,4,4,4,4]
    # - horizontal spacing s; vertical spacing v = s*sqrt(3)/2
    # - rows centered vertically around 0.5
    # - horizontally, alternate rows are shifted by a half-step
    def hex_seed(pattern: List[int], s: float) -> np.ndarray:
        v = s * (math.sqrt(3.0) / 2.0)
        centers: List[Tuple[float, float]] = []
        for k, n_k in enumerate(pattern):
            yk = 0.5 + (k - 2) * v
            row_shift = ((k % 2) - 0.5) * 0.5 * s
            for m in range(n_k):
                xk = 0.5 + row_shift + (m - (n_k - 1) / 2.0) * s
                centers.append((xk, yk))
        centers_arr = np.array(centers, dtype=float)
        # Clip into the unit square interior (leaving a tiny margin for jitter room)
        centers_arr = np.clip(centers_arr, 1e-3, 1 - 1e-3)
        return centers_arr

    # Utility to get (row_idx, col_idx) for each center as created by hex_seed
    def pattern_row_col_indices(pattern: List[int]) -> Tuple[np.ndarray, np.ndarray]:
        rows = []
        cols = []
        for k, n_k in enumerate(pattern):
            for m in range(n_k):
                rows.append(k)
                cols.append(m)
        return np.array(rows, dtype=int), np.array(cols, dtype=int)

    # Structured, boundary-aware jitter:
    # - outer rows (k=0 or 4): nudge inward by 1e-3 along y toward center
    # - inner rows (k=1,2,3): nudge outward by 5e-4 along y away from center
    # - outer columns (m=0 or m=n_k-1): nudge inward by 1e-3 along x toward center
    # - inner columns: nudge outward by 5e-4 along x away from center
    def structured_boundary_jitter(centers_base: np.ndarray, pattern: List[int]) -> np.ndarray:
        centers = centers_base.copy()
        row_idx, col_idx = pattern_row_col_indices(pattern)

        # For columns, we need n_k per row to test m == n_k-1
        n_per_row = np.array(pattern, dtype=int)
        # Build an array of n_k indexed by each point
        n_k_for_point = n_per_row[row_idx]

        # Inward/outward directions relative to center 0.5
        x = centers[:, 0]
        y = centers[:, 1]

        # Initialize offsets
        dx = np.zeros_like(x)
        dy = np.zeros_like(y)

        # Rows
        outer_row_mask = (row_idx == 0) | (row_idx == 4)
        inner_row_mask = ~outer_row_mask

        dy[outer_row_mask] = 1e-3 * np.sign(0.5 - y[outer_row_mask])
        dy[inner_row_mask] = 5e-4 * np.sign(y[inner_row_mask] - 0.5)

        # Columns
        outer_col_mask = (col_idx == 0) | (col_idx == (n_k_for_point - 1))
        inner_col_mask = ~outer_col_mask

        dx[outer_col_mask] = 1e-3 * np.sign(0.5 - x[outer_col_mask])
        dx[inner_col_mask] = 5e-4 * np.sign(x[inner_col_mask] - 0.5)

        centers[:, 0] = np.clip(x + dx, 1e-3, 1 - 1e-3)
        centers[:, 1] = np.clip(y + dy, 1e-3, 1 - 1e-3)
        return centers

    # Per-seed pipeline: LP bootstrap → barrier ascent (with periodic LP) → LP → uniform inflation #1 → LP
    def run_barrier_from_seed(centers_init: np.ndarray) -> np.ndarray:
        # Initialize small radii to ensure a strictly feasible start
        r0 = 0.01
        x = centers_init[:, 0].copy()
        y = centers_init[:, 1].copy()
        r = np.full(N, r0, dtype=float)

        # Barrier ascent hyperparameters
        mu_schedule = [5e-2, 2e-2, 1e-2, 5e-3, 3e-3, 1e-3]
        iters_per_mu_base = 300
        base_step = 0.05
        min_step = 1e-8
        backtrack = 0.5

        def compute_slacks(xv, yv, rv):
            # Pairwise distances
            dx = xv[pair_i] - xv[pair_j]
            dy = yv[pair_i] - yv[pair_j]
            d = np.hypot(dx, dy)
            d = np.maximum(d, tiny)
            s_pairs = d - (rv[pair_i] + rv[pair_j] + eps)

            # Boundary slacks
            s_xL = xv - rv - eps
            s_xR = 1.0 - rv - eps - xv
            s_yB = yv - rv - eps
            s_yT = 1.0 - rv - eps - yv
            # Positive radii
            s_r = rv

            return (dx, dy, d, s_pairs, s_xL, s_xR, s_yB, s_yT, s_r)

        def feasible(slacks) -> bool:
            # All slacks must be strictly positive for barrier
            for arr in slacks[3:]:
                if np.any(arr <= 0.0):
                    return False
            return True

        def objective(mu, slacks, rv) -> float:
            # f = sum r + mu * sum log(slacks)
            _, _, _, s_pairs, s_xL, s_xR, s_yB, s_yT, s_r = slacks
            logs = (
                np.sum(np.log(s_pairs))
                + np.sum(np.log(s_xL))
                + np.sum(np.log(s_xR))
                + np.sum(np.log(s_yB))
                + np.sum(np.log(s_yT))
                + np.sum(np.log(s_r))
            )
            return float(np.sum(rv)) + mu * float(logs)

        # Ensure initial feasibility; if not, shrink radii and recenter slightly
        slacks = compute_slacks(x, y, r)
        if not feasible(slacks):
            r = np.full(N, 0.005, dtype=float)
            slacks = compute_slacks(x, y, r)
            if not feasible(slacks):
                x = 0.5 + 0.8 * (x - 0.5)
                y = 0.5 + 0.8 * (y - 0.5)
                r = np.full(N, 0.003, dtype=float)
                slacks = compute_slacks(x, y, r)

        # LP bootstrap (fixed centers) to accelerate radii allocation
        centers_now = np.column_stack((x, y))
        r_lp0 = lp_inflate(centers_now, eps)
        if r_lp0 is not None and np.sum(r_lp0) > np.sum(r) + tiny:
            r = r_lp0
            # Tiny uniform shrink to restore strict inequalities, then re-clip centers
            r *= 0.999
            x = np.clip(x, r + eps, 1.0 - r - eps)
            y = np.clip(y, r + eps, 1.0 - r - eps)
            slacks = compute_slacks(x, y, r)
            if not feasible(slacks):
                # If any slack is too tight numerically, shrink a hair more
                r *= 0.999
                x = np.clip(x, r + eps, 1.0 - r - eps)
                y = np.clip(y, r + eps, 1.0 - r - eps)
                slacks = compute_slacks(x, y, r)

        # Barrier ascent with periodic LP inflations after each mu-stage
        prev_near_active = None
        boost_last_two = False
        last_two_start = len(mu_schedule) - 2 if len(mu_schedule) >= 2 else 0

        for idx_mu, mu in enumerate(mu_schedule):
            # If near-active pairwise constraints increased since previous stage,
            # boost iterations for the last two μ values by 20% for this seed.
            curr_near_active = int(np.count_nonzero(slacks[3] < 2e-4))
            if prev_near_active is not None and curr_near_active > prev_near_active:
                boost_last_two = True
            prev_near_active = curr_near_active

            stage_iters = iters_per_mu_base
            if boost_last_two and idx_mu >= last_two_start:
                stage_iters = int(math.ceil(iters_per_mu_base * 1.2))

            for _ in range(stage_iters):
                dx_, dy_, d_, s_pairs, s_xL, s_xR, s_yB, s_yT, s_r = slacks

                # Gradients initialization
                grad_x = np.zeros(N, dtype=float)
                grad_y = np.zeros(N, dtype=float)
                grad_r = np.ones(N, dtype=float)  # from sum(r_i)

                # Pairwise constraints gradient
                coef = mu / (s_pairs * d_)
                np.add.at(grad_x, pair_i, coef * dx_)
                np.add.at(grad_x, pair_j, -coef * dx_)
                np.add.at(grad_y, pair_i, coef * dy_)
                np.add.at(grad_y, pair_j, -coef * dy_)
                coef_r = mu / s_pairs
                np.add.at(grad_r, pair_i, -coef_r)
                np.add.at(grad_r, pair_j, -coef_r)

                # Boundary constraints gradients via slacks
                grad_x += mu / s_xL
                grad_r += -mu / s_xL
                grad_x += -mu / s_xR
                grad_r += -mu / s_xR
                grad_y += mu / s_yB
                grad_r += -mu / s_yB
                grad_y += -mu / s_yT
                grad_r += -mu / s_yT
                # Positivity: s_r = r
                grad_r += mu / s_r

                # Backtracking line search for feasibility and ascent
                step = base_step
                f_curr = objective(mu, slacks, r)
                improved = False
                for _bt in range(40):
                    xn = x + step * grad_x
                    yn = y + step * grad_y
                    rn = r + step * grad_r
                    # Compute new slacks
                    slacks_n = compute_slacks(xn, yn, rn)
                    if feasible(slacks_n):
                        f_new = objective(mu, slacks_n, rn)
                        if f_new >= f_curr:
                            x, y, r = xn, yn, rn
                            slacks = slacks_n
                            improved = True
                            break
                    step *= backtrack
                    if step < min_step:
                        break
                if not improved:
                    base_step *= 0.9
                    if base_step < min_step:
                        break

            # Periodic fixed-center LP inflation after barrier stage (accept only if Σr increases)
            centers_now = np.column_stack((x, y))
            r_lp = lp_inflate(centers_now, eps)
            if r_lp is not None and np.sum(r_lp) > np.sum(r) + tiny:
                r = r_lp
                # Recompute slacks with updated radii; ensure strict feasibility defensively
                slacks = compute_slacks(x, y, r)
                if not feasible(slacks):
                    r *= 0.999
                    slacks = compute_slacks(x, y, r)

        # Final fixed-center LP inflation before uniform scaling
        centers_now = np.column_stack((x, y))
        r_lp = lp_inflate(centers_now, eps)
        if r_lp is not None and np.sum(r_lp) > np.sum(r) + tiny:
            r = r_lp

        # Global uniform inflation about square center (0.5, 0.5)
        def global_uniform_inflation(xv, yv, rv, eps_g):
            # Constraints for each circle i (linearized in t):
            # s_xL' = 0.5 - eps + t*(x - 0.5 - r) >= 0
            # s_xR' = 0.5 - eps - t*(x - 0.5 + r) >= 0
            # s_yB' = 0.5 - eps + t*(y - 0.5 - r) >= 0
            # s_yT' = 0.5 - eps - t*(y - 0.5 + r) >= 0
            A = 0.5 - eps_g
            B_xL = xv - 0.5 - rv
            B_xR = -(xv - 0.5 + rv)
            B_yB = yv - 0.5 - rv
            B_yT = -(yv - 0.5 + rv)

            t_ubs = []

            def append_bounds(B_arr):
                # For B < 0, the inequality imposes t <= -A/B
                mask = B_arr < 0
                if np.any(mask):
                    t_ubs.extend(list((-A / B_arr[mask])))

            append_bounds(B_xL)
            append_bounds(B_xR)
            append_bounds(B_yB)
            append_bounds(B_yT)

            if len(t_ubs) == 0:
                # No active bounds -> scaling wouldn't change
                return xv, yv, rv

            t_max = min(t_ubs)
            if t_max > 1.0 + 1e-12:
                t = t_max * (1.0 - 1e-12)
                xv2 = 0.5 + t * (xv - 0.5)
                yv2 = 0.5 + t * (yv - 0.5)
                rv2 = t * rv
                return xv2, yv2, rv2
            else:
                return xv, yv, rv

        # Acceptance-gated uniform inflation #1 + LP
        sum_before = float(np.sum(r))
        x_old, y_old, r_old = x.copy(), y.copy(), r.copy()
        x_inf, y_inf, r_inf = global_uniform_inflation(x, y, r, eps)
        centers_now = np.column_stack((x_inf, y_inf))
        r_lp = lp_inflate(centers_now, eps)
        if r_lp is not None and np.sum(r_lp) > np.sum(r_inf) + tiny:
            r_inf = r_lp
        if float(np.sum(r_inf)) > sum_before + tiny:
            x, y, r = x_inf, y_inf, r_inf
        else:
            x, y, r = x_old, y_old, r_old

        # Final polishing: small projection to ensure strict feasibility
        r = np.maximum(r, 1e-6)
        x = np.clip(x, r + eps, 1.0 - r - eps)
        y = np.clip(y, r + eps, 1.0 - r - eps)

        # Pairwise safety shrink passes to remove any tiny overlap violations
        for _ in range(3):
            dxp = x[pair_i] - x[pair_j]
            dyp = y[pair_i] - y[pair_j]
            d = np.hypot(dxp, dyp)
            need = r[pair_i] + r[pair_j] + eps
            viol = need - d
            if np.all(viol <= 0):
                break
            for k in range(len(pair_i)):
                if viol[k] > 0:
                    i = pair_i[k]; j = pair_j[k]
                    if r[i] >= r[j]:
                        r[i] = max(r[i] - 0.5 * viol[k], 1e-6)
                    else:
                        r[j] = max(r[j] - 0.5 * viol[k], 1e-6)
            x = np.clip(x, r + eps, 1.0 - r - eps)
            y = np.clip(y, r + eps, 1.0 - r - eps)

        return np.column_stack((x, y, r))

    # Multi-start hex seeding with diversification:
    # Patterns expanded to broaden contact graphs
    patterns = [
        [5, 4, 4, 4, 4],
        [4, 5, 4, 4, 4],
        [4, 4, 5, 4, 4],
        [4, 4, 4, 5, 4],
        [5, 4, 5, 4, 3],
        [5, 5, 4, 4, 3],
        [4, 5, 5, 4, 3],
        # Augmented patterns per mutation
        [5, 5, 5, 3, 3],
        [6, 4, 4, 4, 3],
    ]
    # Lattice spacings around 0.2 with broader variation
    spacings = [0.2 * 0.96, 0.2 * 0.98, 0.2, 0.2 * 1.02, 0.2 * 1.04]

    best_circles = None
    best_sum = -1.0

    rng_global = np.random.default_rng()

    for pat in patterns:
        for s in spacings:
            # Base seed with true hex staggering and no jitter
            centers_base = hex_seed(pat, s)

            # Build 6 jittered seeds per (pattern, s):
            # - three with magnitude ±1e-3
            # - two with magnitude ±2e-3
            # - one structured, boundary-aware jitter
            seeds = []
            for _ in range(3):
                jitter = rng_global.uniform(-1e-3, 1e-3, size=centers_base.shape)
                centers_j = np.clip(centers_base + jitter, 1e-3, 1 - 1e-3)
                seeds.append(centers_j)
            for _ in range(2):
                jitter = rng_global.uniform(-2e-3, 2e-3, size=centers_base.shape)
                centers_j = np.clip(centers_base + jitter, 1e-3, 1 - 1e-3)
                seeds.append(centers_j)
            # Structured jitter
            structured = structured_boundary_jitter(centers_base, pat)
            seeds.append(structured)

            # LP-only preselection: run fixed-center LP once per jittered seed to score Σr_LP,
            # keep the top-2 seeds; if LP unavailable, fall back to heuristic score and keep two best by that
            lp_scores = []
            lp_available = True
            for seed in seeds:
                r_lp = lp_inflate(seed, eps)
                if r_lp is None:
                    lp_available = False
                    break
                lp_scores.append(float(np.sum(r_lp)))

            survivors = []
            if lp_available:
                # Select indices of top-2 by LP score
                idx_sorted = np.argsort(lp_scores)[::-1]
                top2_idx = idx_sorted[:2].tolist()
                survivors = [seeds[i] for i in top2_idx]
            else:
                # Fallback: simple boundary cap sum as heuristic score, keep any two
                # (no preselection advantage when LP is missing)
                m_caps = [np.minimum.reduce([sd[:, 0], 1.0 - sd[:, 0], sd[:, 1], 1.0 - sd[:, 1]]) for sd in seeds]
                scores = [float(np.sum(mc)) for mc in m_caps]
                idx_sorted = np.argsort(scores)[::-1]
                top2_idx = idx_sorted[:2].tolist()
                survivors = [seeds[i] for i in top2_idx]

            # Run the pipeline for survivors and keep the best by sum of radii
            local_best = None
            local_best_sum = -1.0
            for seed in survivors:
                circles = run_barrier_from_seed(seed)
                sum_r = float(np.sum(circles[:, 2]))
                if sum_r > local_best_sum + tiny:
                    local_best_sum = sum_r
                    local_best = circles

            if local_best is not None and local_best_sum > best_sum + tiny:
                best_sum = local_best_sum
                best_circles = local_best

    assert best_circles is not None

    # LP–mini-barrier–LP sandwich refinement on the best solution across seeds.
    # Accept only if Σr increases, with optional uniform inflation #2 + LP.
    def lp_mini_barrier_lp_refine(circles_in: np.ndarray) -> np.ndarray:
        x = circles_in[:, 0].copy()
        y = circles_in[:, 1].copy()
        r = circles_in[:, 2].copy()

        sum_orig = float(np.sum(r))
        best_x, best_y, best_r = x.copy(), y.copy(), r.copy()
        best_sum = sum_orig

        # Utility subroutines (same structure as in main barrier)
        def compute_slacks(xv, yv, rv):
            dx = xv[pair_i] - xv[pair_j]
            dy = yv[pair_i] - yv[pair_j]
            d = np.hypot(dx, dy)
            d = np.maximum(d, tiny)
            s_pairs = d - (rv[pair_i] + rv[pair_j] + eps)
            s_xL = xv - rv - eps
            s_xR = 1.0 - rv - eps - xv
            s_yB = yv - rv - eps
            s_yT = 1.0 - rv - eps - yv
            s_r = rv
            return (dx, dy, d, s_pairs, s_xL, s_xR, s_yB, s_yT, s_r)

        def feasible(slacks) -> bool:
            for arr in slacks[3:]:
                if np.any(arr <= 0.0):
                    return False
            return True

        # LP1 with frozen centers
        centers_now = np.column_stack((x, y))
        r_lp = lp_inflate(centers_now, eps)
        if r_lp is not None and np.sum(r_lp) > np.sum(r) + tiny:
            r = r_lp
            # Update best with LP1 if improved
            if float(np.sum(r)) > best_sum + tiny:
                best_x, best_y, best_r = x.copy(), y.copy(), r.copy()
                best_sum = float(np.sum(r))

        # Slight shrink to ensure strict feasibility for barrier (avoid log(0))
        r *= 0.999999
        slacks = compute_slacks(x, y, r)
        if not feasible(slacks):
            r *= 0.999
            slacks = compute_slacks(x, y, r)

        # Mini-barrier polish: short ascent with smaller step sizes
        mu_schedule = [2e-3, 1e-3, 5e-4]
        iters_per_mu = 60
        base_step = 0.02
        min_step = 1e-8
        backtrack = 0.5

        for mu in mu_schedule:
            for _ in range(iters_per_mu):
                dx_, dy_, d_, s_pairs, s_xL, s_xR, s_yB, s_yT, s_r = slacks

                grad_x = np.zeros(N, dtype=float)
                grad_y = np.zeros(N, dtype=float)
                grad_r = np.ones(N, dtype=float)

                coef = mu / (s_pairs * d_)
                np.add.at(grad_x, pair_i, coef * dx_)
                np.add.at(grad_x, pair_j, -coef * dx_)
                np.add.at(grad_y, pair_i, coef * dy_)
                np.add.at(grad_y, pair_j, -coef * dy_)
                coef_r = mu / s_pairs
                np.add.at(grad_r, pair_i, -coef_r)
                np.add.at(grad_r, pair_j, -coef_r)

                grad_x += mu / s_xL
                grad_r += -mu / s_xL
                grad_x += -mu / s_xR
                grad_r += -mu / s_xR
                grad_y += mu / s_yB
                grad_r += -mu / s_yB
                grad_y += -mu / s_yT
                grad_r += -mu / s_yT
                grad_r += mu / s_r

                # Current barrier objective for comparison
                logs = (
                    np.sum(np.log(s_pairs))
                    + np.sum(np.log(s_xL))
                    + np.sum(np.log(s_xR))
                    + np.sum(np.log(s_yB))
                    + np.sum(np.log(s_yT))
                    + np.sum(np.log(s_r))
                )
                f_curr = float(np.sum(r)) + mu * float(logs)

                step = base_step
                improved = False
                for _bt in range(40):
                    xn = x + step * grad_x
                    yn = y + step * grad_y
                    rn = r + step * grad_r
                    slacks_n = compute_slacks(xn, yn, rn)
                    if feasible(slacks_n):
                        logs_n = (
                            np.sum(np.log(slacks_n[3]))
                            + np.sum(np.log(slacks_n[4]))
                            + np.sum(np.log(slacks_n[5]))
                            + np.sum(np.log(slacks_n[6]))
                            + np.sum(np.log(slacks_n[7]))
                            + np.sum(np.log(slacks_n[8]))
                        )
                        f_new = float(np.sum(rn)) + mu * float(logs_n)
                        if f_new >= f_curr:
                            x, y, r = xn, yn, rn
                            slacks = slacks_n
                            improved = True
                            break
                    step *= backtrack
                    if step < min_step:
                        break
                if not improved:
                    base_step *= 0.9
                    if base_step < min_step:
                        break

        # LP2 with frozen centers; keep if improves best_sum
        centers_now = np.column_stack((x, y))
        r_lp2 = lp_inflate(centers_now, eps)
        if r_lp2 is not None and np.sum(r_lp2) > np.sum(r) + tiny:
            r = r_lp2
        if float(np.sum(r)) > best_sum + tiny:
            best_x, best_y, best_r = x.copy(), y.copy(), r.copy()
            best_sum = float(np.sum(r))

        # Optional uniform inflation #2 + LP; accept only if Σr increases
        def global_uniform_inflation(xv, yv, rv, eps_g):
            A = 0.5 - eps_g
            B_xL = xv - 0.5 - rv
            B_xR = -(xv - 0.5 + rv)
            B_yB = yv - 0.5 - rv
            B_yT = -(yv - 0.5 + rv)

            t_ubs = []

            def append_bounds(B_arr):
                mask = B_arr < 0
                if np.any(mask):
                    t_ubs.extend(list((-A / B_arr[mask])))

            append_bounds(B_xL)
            append_bounds(B_xR)
            append_bounds(B_yB)
            append_bounds(B_yT)

            if len(t_ubs) == 0:
                return xv, yv, rv

            t_max = min(t_ubs)
            if t_max > 1.0 + 1e-12:
                t = t_max * (1.0 - 1e-12)
                xv2 = 0.5 + t * (xv - 0.5)
                yv2 = 0.5 + t * (yv - 0.5)
                rv2 = t * rv
                return xv2, yv2, rv2
            else:
                return xv, yv, rv

        x2, y2, r2 = global_uniform_inflation(best_x, best_y, best_r, eps)
        centers_now2 = np.column_stack((x2, y2))
        r_lp3 = lp_inflate(centers_now2, eps)
        if r_lp3 is not None and np.sum(r_lp3) > np.sum(r2) + tiny:
            r2 = r_lp3
        if float(np.sum(r2)) > best_sum + tiny:
            best_x, best_y, best_r = x2, y2, r2
            best_sum = float(np.sum(best_r))

        # Final safety projection and overlap polish (strict feasibility)
        best_r = np.maximum(best_r, 1e-6)
        best_x = np.clip(best_x, best_r + eps, 1.0 - best_r - eps)
        best_y = np.clip(best_y, best_r + eps, 1.0 - best_r - eps)
        for _ in range(3):
            dxp = best_x[pair_i] - best_x[pair_j]
            dyp = best_y[pair_i] - best_y[pair_j]
            d = np.hypot(dxp, dyp)
            need = best_r[pair_i] + best_r[pair_j] + eps
            viol = need - d
            if np.all(viol <= 0):
                break
            for k in range(len(pair_i)):
                if viol[k] > 0:
                    i = pair_i[k]; j = pair_j[k]
                    if best_r[i] >= best_r[j]:
                        best_r[i] = max(best_r[i] - 0.5 * viol[k], 1e-6)
                    else:
                        best_r[j] = max(best_r[j] - 0.5 * viol[k], 1e-6)
            best_x = np.clip(best_x, best_r + eps, 1.0 - best_r - eps)
            best_y = np.clip(best_y, best_r + eps, 1.0 - best_r - eps)

        # Return improved solution if any; otherwise the input
        if best_sum > sum_orig + tiny:
            return np.column_stack((best_x, best_y, best_r))
        else:
            return circles_in

    # Apply refinement and accept only if Σr increases (guard inside function already ensures monotonicity).
    refined = lp_mini_barrier_lp_refine(best_circles)
    if np.sum(refined[:, 2]) > np.sum(best_circles[:, 2]) + tiny:
        best_circles = refined

    return best_circles
# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```

```python
#!/usr/bin/env python3
"""High-density packing of 21 disjoint circles inside the unit square.

Algorithm overview (integrated from the provided description):
- Reduce the perimeter constraint (width + height ≤ 2) to packing inside the unit square [0,1]^2.
- Multi-start, multi-scale near-hexagonal seeding with several row-shift patterns and spacings.
- LP-only preselection: evaluate jittered seeds with one fixed-center LP and keep only top-K survivors.
- Initialize equal radii; run an LP bootstrap to globally re-optimize radii on fixed centers.
- Iteratively grow radii using a coordinated, feasibility-aware update and gently move centers
  using repulsive forces from near neighbors and boundaries, capped by an adaptive trust region.
- Periodically, freeze centers and solve a small linear program (LP) to globally re-optimize radii
  exactly for those fixed centers (lp_inflate). Accept LP solutions only when they strictly improve
  the sum of radii.
- Global uniform inflation about the square center to convert boundary slack into larger radii, then LP.
- Final LP–mini-barrier–LP sandwich polish: LP, a short interior-barrier ascent keeping strict feasibility,
  then LP again; accept only if the sum of radii increases.
- After each major step, accept changes only when the sum increases; finish with a tiny global shrink.
"""

import json
import math
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 21):
    """Construct a dense packing of 21 circles inside the unit square.

    Returns:
        ndarray of shape (21, 3) with columns [x, y, r], all strictly feasible.
    """

    # Numerical tolerances
    eps = 1e-6   # strict feasibility slack used in separation/LP constraints
    final_clearance = 1e-9  # ensure strictly feasible output by tiny shrink

    rng = np.random.RandomState(42)

    assert num_circles == 21, "This implementation targets exactly 21 circles."

    # Seeding patterns: five rows with row counts summing to 21
    # Place a single 5-count row among four 4-count rows at different positions
    patterns = [
        [5, 4, 4, 4, 4],
        [4, 5, 4, 4, 4],
        [4, 4, 5, 4, 4],
        [4, 4, 4, 5, 4],
    ]

    def hex_seed(row_counts: List[int], s: float) -> np.ndarray:
        """Generate centers on a clipped hexagonal lattice for the given row counts.

        Args:
            row_counts: list of 5 integers, each 4 or 5, summing to 21.
            s: horizontal spacing between lattice points.

        Returns:
            centers: ndarray of shape (21, 2)
        """
        v = (math.sqrt(3) / 2.0) * s  # vertical spacing for hex lattice rows
        nrows = len(row_counts)
        # Symmetric vertical placement
        y0 = 0.5 - ((nrows - 1) * v) / 2.0
        centers = []
        for k, m in enumerate(row_counts):
            y = y0 + k * v
            # Alternate rows are shifted by s/2 in hexagonal lattice; here we place 5-count rows centered
            if m == 5:
                xs = 0.5 * s + np.arange(m) * s  # 0.5*s, 1.5*s, ..., 4.5*s for s grid
            elif m == 4:
                xs = s + np.arange(m) * s       # s, 2s, 3s, 4s
            else:
                raise ValueError("Unsupported row count in pattern")
            for x in xs:
                centers.append([x, y])
        centers = np.array(centers, dtype=float)
        assert centers.shape[0] == num_circles
        return centers

    def min_pairwise_distance(centers: np.ndarray) -> float:
        """Compute the minimum Euclidean distance between distinct centers."""
        n = centers.shape[0]
        min_d = np.inf
        for i in range(n):
            dx = centers[i, 0] - centers[:, 0]
            dy = centers[i, 1] - centers[:, 1]
            d = np.hypot(dx, dy)
            d[i] = np.inf
            m = np.min(d)
            if m < min_d:
                min_d = m
        return float(min_d)

    def initialize_radii(centers: np.ndarray) -> np.ndarray:
        """Compute a safe initial equal radius based on nearest-neighbor and boundary gaps."""
        # Initial centers are inside the unit square by construction; start with equal radii.
        min_d = min_pairwise_distance(centers)
        # Boundary margins for r=0 are simply min(x, 1-x, y, 1-y)
        base_margin = float(np.min(np.minimum.reduce([centers[:, 0], 1.0 - centers[:, 0], centers[:, 1], 1.0 - centers[:, 1]])))
        r0 = min(min_d / 2.0, base_margin) - 1e-6
        r0 = max(r0, 1e-6)
        return np.full(centers.shape[0], r0, dtype=float)

    def resolve_overlaps(centers: np.ndarray, radii: np.ndarray, max_iter: int = 1000) -> None:
        """Iteratively separate overlapping circle pairs, clamping to boundary feasibility."""
        n = centers.shape[0]
        for _ in range(max_iter):
            any_overlap = False
            for i in range(n):
                for j in range(i + 1, n):
                    dx = centers[j, 0] - centers[i, 0]
                    dy = centers[j, 1] - centers[i, 1]
                    d = math.hypot(dx, dy)
                    needed = radii[i] + radii[j] + eps
                    if d < needed:
                        any_overlap = True
                        # Push apart by half the overlap along the line of centers
                        if d < 1e-12:
                            # Centers coincide; choose a random direction
                            theta = rng.uniform(0.0, 2.0 * math.pi)
                            ux, uy = math.cos(theta), math.sin(theta)
                        else:
                            ux, uy = dx / d, dy / d
                        push = 0.5 * (needed - d)
                        centers[i, 0] -= ux * push
                        centers[i, 1] -= uy * push
                        centers[j, 0] += ux * push
                        centers[j, 1] += uy * push
                        # Clamp back to boundary feasibility
                        for k in (i, j):
                            centers[k, 0] = min(max(centers[k, 0], radii[k] + eps), 1.0 - radii[k] - eps)
                            centers[k, 1] = min(max(centers[k, 1], radii[k] + eps), 1.0 - radii[k] - eps)
            if not any_overlap:
                break

    def feasibility_projection(centers: np.ndarray, radii: np.ndarray) -> None:
        """Project centers back to satisfy boundary constraints exactly."""
        centers[:, 0] = np.minimum(np.maximum(centers[:, 0], radii + eps), 1.0 - radii - eps)
        centers[:, 1] = np.minimum(np.maximum(centers[:, 1], radii + eps), 1.0 - radii - eps)

    def compute_pairwise_slacks(centers: np.ndarray, radii: np.ndarray) -> np.ndarray:
        """Compute pairwise slacks s_ij = d_ij - (r_i + r_j)."""
        n = centers.shape[0]
        slacks = np.empty((n, n), dtype=float)
        for i in range(n):
            dx = centers[i, 0] - centers[:, 0]
            dy = centers[i, 1] - centers[:, 1]
            d = np.hypot(dx, dy)
            slacks[i, :] = d - (radii[i] + radii)
            slacks[i, i] = np.inf
        return slacks

    def lp_inflate(centers: np.ndarray):
        """Fixed-center LP: maximize sum_i r_i subject to:
           - 0 <= r_i <= m_i where m_i = min(x_i, 1-x_i, y_i, 1-y_i)
           - r_i + r_j <= d_ij - eps for all i < j
           Uses scipy.optimize.linprog if available. Returns optimized radii or None on failure.
        """
        # Lazy import to keep robustness if SciPy is unavailable.
        try:
            from scipy.optimize import linprog
        except Exception:
            return None

        n = centers.shape[0]
        # Boundary caps
        m = np.minimum.reduce([centers[:, 0], 1.0 - centers[:, 0], centers[:, 1], 1.0 - centers[:, 1]])
        m = np.maximum(m, 0.0)  # ensure nonnegative bounds

        # Objective: maximize sum r_i  <=> minimize -sum r_i
        c = -np.ones(n, dtype=float)

        # Pairwise constraints: r_i + r_j <= d_ij - eps
        # Build A_ub and b_ub
        rows = []
        rhs = []
        for i in range(n):
            for j in range(i + 1, n):
                dx = centers[i, 0] - centers[j, 0]
                dy = centers[i, 1] - centers[j, 1]
                dij = math.hypot(dx, dy)
                b = max(0.0, dij - eps)  # clamp to 0 for robustness if centers coincide
                row = np.zeros(n, dtype=float)
                row[i] = 1.0
                row[j] = 1.0
                rows.append(row)
                rhs.append(b)
        if rows:
            A_ub = np.vstack(rows)
            b_ub = np.array(rhs, dtype=float)
        else:
            A_ub = None
            b_ub = None

        # Variable bounds
        bounds = [(0.0, float(mi)) for mi in m]

        # Solve with HiGHS
        try:
            res = linprog(c=c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        except Exception:
            return None

        if not res.success or res.x is None:
            return None

        radii_opt = np.asarray(res.x, dtype=float)
        # Numerical guard: clip slightly within bounds
        radii_opt = np.minimum(radii_opt, m)
        radii_opt = np.maximum(radii_opt, 0.0)
        return radii_opt

    def growth_and_move(centers: np.ndarray, radii: np.ndarray, iterations: int = 300) -> Tuple[np.ndarray, np.ndarray]:
        """Iterative growth of radii and gentle center moves with adaptive trust-region control.

        - Coordinate-ascent radii growth from per-circle slack (boundaries + neighbors).
        - Gentle center moves from boundary and neighbor repulsions, capped by trust radius.
        - Periodic exact LP inflation on fixed centers.
        - Blockwise adaptive trust radius: shrinks only after a full block without improvement,
          gently grows after improvements. LP gains count as improvements via best_sum tracking.

        Returns:
            best_centers, best_radii for the best sum of radii encountered.
        """
        n = centers.shape[0]
        trust_radius = 0.02
        near_thresh = 0.03  # thresholds for activating constraints (neighbors/boundary)
        beta = 0.4  # fraction of available slack to consume per coordinate ascent step
        K = 50      # LP correction frequency
        B = 50      # block size for trust-radius controller

        best_centers = centers.copy()
        best_radii = radii.copy()
        best_sum = float(np.sum(best_radii))
        last_improve_it = -10**9  # sentinel: far in the past

        for it in range(iterations):
            # Compute slacks
            slacks = compute_pairwise_slacks(centers, radii)
            # Per-circle minimal slack including boundaries
            boundary_slacks = np.minimum.reduce([
                centers[:, 0] - radii,
                1.0 - radii - centers[:, 0],
                centers[:, 1] - radii,
                1.0 - radii - centers[:, 1],
            ])
            min_pair_slack = np.min(slacks, axis=1)
            avail_slack = np.minimum(boundary_slacks, min_pair_slack)

            # Coordinate ascent on radii: increase one-by-one respecting current active constraints
            order = np.argsort(-avail_slack)  # descending order by available slack
            delta_r = np.zeros(n, dtype=float)
            for idx in order:
                # Updated boundary slack with current delta
                bslack = min(
                    centers[idx, 0] - (radii[idx] + delta_r[idx]),
                    1.0 - (radii[idx] + delta_r[idx]) - centers[idx, 0],
                    centers[idx, 1] - (radii[idx] + delta_r[idx]),
                    1.0 - (radii[idx] + delta_r[idx]) - centers[idx, 1],
                )
                # Updated pairwise slacks with current delta for previously updated indices
                smax = bslack
                for j in range(n):
                    if j == idx:
                        continue
                    sij = slacks[idx, j] - (delta_r[idx] + delta_r[j])
                    if sij < smax:
                        smax = sij
                # Propose increase within radius slack
                dr = max(0.0, beta * smax)
                delta_r[idx] += dr

            # Apply delta radii
            radii += delta_r

            # Gentle center moves to relieve near constraints
            move = np.zeros_like(centers)
            for i in range(n):
                # Boundary pushes
                left = centers[i, 0] - radii[i]
                right = 1.0 - radii[i] - centers[i, 0]
                bottom = centers[i, 1] - radii[i]
                top = 1.0 - radii[i] - centers[i, 1]
                # Push inside if close to boundary
                if left < near_thresh:
                    move[i, 0] += (near_thresh - left)
                if right < near_thresh:
                    move[i, 0] -= (near_thresh - right)
                if bottom < near_thresh:
                    move[i, 1] += (near_thresh - bottom)
                if top < near_thresh:
                    move[i, 1] -= (near_thresh - top)

                # Neighbor pushes
                for j in range(n):
                    if j == i:
                        continue
                    dx = centers[i, 0] - centers[j, 0]
                    dy = centers[i, 1] - centers[j, 1]
                    dij = math.hypot(dx, dy)
                    sij = dij - (radii[i] + radii[j])
                    # Active only for near interactions
                    if sij < near_thresh:
                        # Push i away from j
                        if dij < 1e-12:
                            # Random direction if coincident
                            theta = rng.uniform(0.0, 2.0 * math.pi)
                            ux, uy = math.cos(theta), math.sin(theta)
                        else:
                            ux, uy = dx / dij, dy / dij
                        strength = (near_thresh - sij)
                        move[i, 0] += ux * strength * 0.5
                        move[i, 1] += uy * strength * 0.5

            # Trust-region on movement
            norms = np.hypot(move[:, 0], move[:, 1])
            scale = np.minimum(1.0, np.divide(trust_radius, norms, out=np.ones_like(norms), where=norms > 0))
            centers += move * scale[:, None]

            # Project to feasibility, then polish overlaps
            feasibility_projection(centers, radii)
            resolve_overlaps(centers, radii, max_iter=200)

            # Periodic exact LP inflation on fixed centers
            if (it + 1) % K == 0:
                r_lp = lp_inflate(centers)
                if r_lp is not None:
                    sum_before = float(np.sum(radii))
                    sum_after = float(np.sum(r_lp))
                    if sum_after > sum_before + 1e-12:
                        radii = r_lp
                        feasibility_projection(centers, radii)

            # Keep best feasible solution so far (sum of radii)
            current_sum = float(np.sum(radii))
            if current_sum > best_sum + 1e-12:
                best_sum = current_sum
                best_centers = centers.copy()
                best_radii = radii.copy()
                last_improve_it = it  # mark improvement for adaptive controller

            # Adaptive trust radius controller: operate blockwise
            if (it + 1) % B == 0:
                if (it - last_improve_it) >= (B - 1):
                    # No improvement in last full block: shrink
                    trust_radius = max(0.7 * trust_radius, 1e-4)
                else:
                    # Improvement observed: gently grow
                    trust_radius = min(1.1 * trust_radius, 0.05)

        return best_centers, best_radii

    def global_uniform_inflation(centers: np.ndarray, radii: np.ndarray) -> None:
        """Uniformly scale (x, y, r) about the square center to saturate a boundary.

        This strictly increases sum of radii and preserves pairwise separations.
        """
        c = 0.5
        n = centers.shape[0]
        s_max = np.inf

        for i in range(n):
            x, y, r = centers[i, 0], centers[i, 1], radii[i]
            # Left: c + s*(x - c - r) >= 0
            aL = x - c - r
            if aL < 0:
                s_max = min(s_max, c / (-aL))
            # Right: c + s*(x - c + r) <= 1
            aR = x - c + r
            if aR > 0:
                s_max = min(s_max, 0.5 / aR)
            # Bottom: c + s*(y - c - r) >= 0
            aB = y - c - r
            if aB < 0:
                s_max = min(s_max, c / (-aB))
            # Top: c + s*(y - c + r) <= 1
            aT = y - c + r
            if aT > 0:
                s_max = min(s_max, 0.5 / aT)

        if not np.isfinite(s_max) or s_max <= 1.0:
            return  # nothing to do

        # Apply scaling with a tiny safety margin
        s = max(1.0, s_max * (1.0 - 1e-12))
        centers[:, 0] = c + s * (centers[:, 0] - c)
        centers[:, 1] = c + s * (centers[:, 1] - c)
        radii *= s
        # No overlaps are introduced by uniform scaling; reproject to boundary for numerical safety.
        feasibility_projection(centers, radii)

    def barrier_objective(centers: np.ndarray, radii: np.ndarray, mu: float) -> float:
        """Compute barrier objective: sum(r_i) + mu * [sum log(boundary slacks) + sum log(pairwise slacks)]."""
        n = centers.shape[0]
        # Boundary slacks
        left = centers[:, 0] - radii
        right = 1.0 - radii - centers[:, 0]
        bottom = centers[:, 1] - radii
        top = 1.0 - radii - centers[:, 1]
        if (left <= eps).any() or (right <= eps).any() or (bottom <= eps).any() or (top <= eps).any():
            return -np.inf
        val = float(np.sum(radii))
        val += mu * float(np.sum(np.log(left) + np.log(right) + np.log(bottom) + np.log(top)))
        # Pairwise slacks
        for i in range(n):
            xi, yi, ri = centers[i, 0], centers[i, 1], radii[i]
            for j in range(i + 1, n):
                dx = xi - centers[j, 0]
                dy = yi - centers[j, 1]
                dij = math.hypot(dx, dy)
                sij = dij - (ri + radii[j])
                if sij <= eps:
                    return -np.inf
                val += mu * math.log(sij)
        return val

    def mini_barrier_polish(centers: np.ndarray, radii: np.ndarray,
                            mu_schedule: List[float] = (5e-4, 2e-4, 1e-4),
                            steps_per_mu: int = 20,
                            init_step: float = 5e-3) -> Tuple[np.ndarray, np.ndarray]:
        """Short interior-barrier ascent with backtracking line search.

        Keeps strict feasibility while nudging centers and radii to tighten near-tangencies,
        aiming to create exploitable slack for a follow-up LP.

        Returns:
            polished_centers, polished_radii
        """
        n = centers.shape[0]
        c = centers.copy()
        r = radii.copy()

        for mu in mu_schedule:
            # perform a few ascent steps at this barrier parameter
            step0 = init_step
            for _ in range(steps_per_mu):
                # Gradients initialization
                grad_x = np.zeros(n, dtype=float)
                grad_y = np.zeros(n, dtype=float)
                grad_r = np.ones(n, dtype=float)  # from sum(r_i)

                # Boundary slacks and their gradient contributions
                left = c[:, 0] - r
                right = 1.0 - r - c[:, 0]
                bottom = c[:, 1] - r
                top = 1.0 - r - c[:, 1]

                # Guard feasibility
                if (left <= eps).any() or (right <= eps).any() or (bottom <= eps).any() or (top <= eps).any():
                    # Project minimally inside feasibility and continue
                    feasibility_projection(c, r)
                    resolve_overlaps(c, r, max_iter=10)
                    # Recompute after projection
                    left = c[:, 0] - r
                    right = 1.0 - r - c[:, 0]
                    bottom = c[:, 1] - r
                    top = 1.0 - r - c[:, 1]

                # Boundary gradient contributions from log barrier
                grad_x += mu * (1.0 / left - 1.0 / right)
                grad_y += mu * (1.0 / bottom - 1.0 / top)
                grad_r += mu * (-1.0 / left - 1.0 / right - 1.0 / bottom - 1.0 / top)

                # Pairwise contributions
                for i in range(n):
                    xi, yi = c[i, 0], c[i, 1]
                    for j in range(i + 1, n):
                        dx = xi - c[j, 0]
                        dy = yi - c[j, 1]
                        dij = math.hypot(dx, dy)
                        if dij < 1e-12:
                            # Skip pathological case; resolve_overlaps will handle
                            continue
                        sij = dij - (r[i] + r[j])
                        if sij <= eps:
                            # Tiny corrective separation push
                            ux, uy = dx / dij, dy / dij
                            sep = (eps - sij) * 0.5
                            c[i, 0] += ux * sep
                            c[i, 1] += uy * sep
                            c[j, 0] -= ux * sep
                            c[j, 1] -= uy * sep
                            # Recompute after correction
                            dx = c[i, 0] - c[j, 0]
                            dy = c[i, 1] - c[j, 1]
                            dij = math.hypot(dx, dy)
                            sij = dij - (r[i] + r[j])
                            if sij <= eps:
                                continue
                        coeff = mu * (1.0 / sij)
                        ux, uy = dx / dij, dy / dij
                        # ∂sij/∂x_i = ux, ∂sij/∂y_i = uy, ∂sij/∂r_i = -1
                        grad_x[i] += coeff * ux
                        grad_y[i] += coeff * uy
                        grad_r[i] += -coeff
                        # symmetric contributions for j
                        grad_x[j] += -coeff * ux
                        grad_y[j] += -coeff * uy
                        grad_r[j] += -coeff

                # Assemble proposed step (ascend along gradient)
                # Normalize step to trust-region magnitude
                max_g = max(np.max(np.abs(grad_x)), np.max(np.abs(grad_y)), np.max(np.abs(grad_r)))
                if not np.isfinite(max_g) or max_g <= 0.0:
                    continue
                alpha = step0 / max_g

                # Backtracking line search to maintain feasibility and improve barrier objective
                base_obj = barrier_objective(c, r, mu)
                accepted = False
                for _bt in range(12):
                    c_new = c.copy()
                    r_new = r.copy()
                    c_new[:, 0] += alpha * grad_x
                    c_new[:, 1] += alpha * grad_y
                    r_new += alpha * grad_r
                    # Keep radii nonnegative and within boundary caps
                    m_caps = np.minimum.reduce([c_new[:, 0], 1.0 - c_new[:, 0], c_new[:, 1], 1.0 - c_new[:, 1]])
                    r_new = np.clip(r_new, 1e-12, m_caps - eps)
                    feasibility_projection(c_new, r_new)
                    # Check barrier objective
                    new_obj = barrier_objective(c_new, r_new, mu)
                    if new_obj > base_obj + 1e-12:
                        c, r = c_new, r_new
                        accepted = True
                        break
                    alpha *= 0.5
                if not accepted:
                    # Reduce step for the next iteration at this mu
                    step0 *= 0.5

                # Small overlap polish for numerical stability
                resolve_overlaps(c, r, max_iter=10)
                feasibility_projection(c, r)

        return c, r

    def run_seed(centers: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Run the optimization pipeline on a given seed; return best feasible packing.

        Integration points:
        - LP bootstrap after initialization.
        - Inside growth_and_move: periodic LP corrections.
        - Global uniform inflation then LP.
        - LP–mini-barrier–LP sandwich polish at the end.
        """
        # Initialize and make strictly feasible
        radii = initialize_radii(centers)
        feasibility_projection(centers, radii)
        resolve_overlaps(centers, radii, max_iter=200)

        # LP bootstrap to remove slack from equal-radius initialization
        r_lp = lp_inflate(centers)
        if r_lp is not None:
            if float(np.sum(r_lp)) > float(np.sum(radii)) + 1e-12:
                radii = r_lp
                feasibility_projection(centers, radii)

        # Trust-region growth and move with periodic LP corrections
        centers, radii = growth_and_move(centers, radii, iterations=300)

        # Global uniform inflation about square center and follow-up LP
        sum_before = float(np.sum(radii))
        global_uniform_inflation(centers, radii)
        r_lp = lp_inflate(centers)
        if r_lp is not None:
            if float(np.sum(r_lp)) > float(np.sum(radii)) + 1e-12:
                radii = r_lp
                feasibility_projection(centers, radii)

        # LP–mini-barrier–LP sandwich polish
        # LP1: fixed centers
        r_lp = lp_inflate(centers)
        if r_lp is not None:
            if float(np.sum(r_lp)) > float(np.sum(radii)) + 1e-12:
                radii = r_lp
                feasibility_projection(centers, radii)

        # Mini-barrier ascent: nudge centers and radii while keeping strict feasibility
        c_bar, r_bar = mini_barrier_polish(centers.copy(), radii.copy())
        # Accept barrier step only if it increases sum of radii
        if float(np.sum(r_bar)) > float(np.sum(radii)) + 1e-12:
            centers, radii = c_bar, r_bar
            feasibility_projection(centers, radii)

        # LP2: convert created slack into radii via LP
        r_lp = lp_inflate(centers)
        if r_lp is not None:
            if float(np.sum(r_lp)) > float(np.sum(radii)) + 1e-12:
                radii = r_lp
                feasibility_projection(centers, radii)

        # Final polish
        resolve_overlaps(centers, radii, max_iter=200)
        feasibility_projection(centers, radii)
        return centers, radii

    # Multi-start: patterns, spacings, jitter; LP-only preselection of seeds per (pattern, s)
    best_centers = None
    best_radii = None
    best_sum = -np.inf

    base_s = 0.2
    spacings = [base_s * 0.98, base_s, base_s * 1.02]
    jitter_count = 6
    top_K = 2  # survivors per (pattern, spacing) after LP-only preselection

    for pattern in patterns:
        for s in spacings:
            base_centers = hex_seed(pattern, s=s)
            # Generate jittered seeds
            jittered = []
            for j in range(jitter_count):
                jitter = (rng.rand(*base_centers.shape) - 0.5) * 0.002
                centers = base_centers + jitter
                centers[:, 0] = np.clip(centers[:, 0], 0.0, 1.0)
                centers[:, 1] = np.clip(centers[:, 1], 0.0, 1.0)
                jittered.append(centers)

            # LP-only preselection: evaluate each jittered seed with a single LP and score by sum of radii
            scores = []
            for centers in jittered:
                r_lp = lp_inflate(centers)
                if r_lp is not None:
                    score = float(np.sum(r_lp))
                else:
                    # Fallback heuristic: sum of boundary caps (upper bounds on radii)
                    m_caps = np.minimum.reduce([centers[:, 0], 1.0 - centers[:, 0], centers[:, 1], 1.0 - centers[:, 1]])
                    score = float(np.sum(np.maximum(m_caps, 0.0)))
                scores.append(score)

            # Select top-K survivors
            idx_sorted = np.argsort(scores)[::-1]
            survivors_idx = idx_sorted[:top_K]
            survivors = [jittered[i] for i in survivors_idx]

            # Run heavy pipeline on survivors
            for centers in survivors:
                c_opt, r_opt = run_seed(centers.copy())
                sum_r = float(np.sum(r_opt))
                if sum_r > best_sum + 1e-12:
                    best_sum = sum_r
                    best_centers = c_opt.copy()
                    best_radii = r_opt.copy()

    # Final polish: enforce tiny clearance and exact feasibility
    feasibility_projection(best_centers, best_radii)
    resolve_overlaps(best_centers, best_radii, max_iter=500)
    feasibility_projection(best_centers, best_radii)
    # Apply tiny shrink to ensure strict clearance on all constraints
    best_radii *= (1.0 - final_clearance)

    return np.column_stack((best_centers, best_radii))


# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```

```python
#!/usr/bin/env python3
"""TriHex++ candidate for packing 21 circles with perimeter-four rectangle constraint.

We pack circles strictly inside the unit square [0,1]^2. That guarantees the
minimum axis-aligned circumscribing rectangle of the circles has width ≤ 1 and
height ≤ 1, hence perimeter ≤ 4.

Pipeline overview:
- Multi-start hexagonal seeding with several five-row patterns that sum to 21,
  varied lattice spacings near 0.2, and small random jitter for symmetry breaking.
- LP-only preselection with fixed centers to keep the best seeds (by sum of radii).
- For each survivor, a growth-and-move phase with adaptive trust region that
  alternates gentle center motions and monotone-radius growth, with periodic
  fixed-center LP inflations to remove structured slack.
- Dual global uniform inflation about (0.5, 0.5) to convert boundary slack into Σr.
- LP–mini-barrier–LP sandwich polish to exploit micro-slack via center nudges.
- Strict monotone acceptance gates and a final tiny shrink ensure feasibility.

The LP solver uses scipy.optimize.linprog if available and falls back to a
deterministic greedy inflation when SciPy is not installed.
"""

import json
import math
import random
from typing import List, Tuple, Optional

import numpy as np

# Try to import SciPy's linprog; fall back gracefully if unavailable.
try:
    from scipy.optimize import linprog as scipy_linprog  # type: ignore

    HAVE_SCIPY = True
except Exception:  # pragma: no cover - environment-dependent
    scipy_linprog = None
    HAVE_SCIPY = False


# ===========================
# Utility and core primitives
# ===========================

def clamp01(x: np.ndarray) -> np.ndarray:
    """Clamp array values into [0,1]."""
    return np.minimum(1.0, np.maximum(0.0, x))


def pairwise_distances(centers: np.ndarray) -> np.ndarray:
    """Compute pairwise Euclidean distances between centers. NxN matrix."""
    # centers: (N, 2)
    diff = centers[:, None, :] - centers[None, :, :]
    d2 = np.sum(diff * diff, axis=2)
    # Avoid tiny negative due to float
    return np.sqrt(np.maximum(d2, 0.0))


def boundary_upper_bounds(centers: np.ndarray) -> np.ndarray:
    """Compute per-circle upper bounds due to square boundary: u_i = min(x,1-x,y,1-y)."""
    x = centers[:, 0]
    y = centers[:, 1]
    return np.minimum(np.minimum(x, 1.0 - x), np.minimum(y, 1.0 - y))


def compute_pairwise_slacks(centers: np.ndarray, radii: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    """Compute pairwise slack matrix s_ij = d_ij - (r_i + r_j) - eps."""
    d = pairwise_distances(centers)
    Rij = radii[:, None] + radii[None, :]
    return d - Rij - eps


def compute_boundary_slacks(centers: np.ndarray, radii: np.ndarray) -> np.ndarray:
    """For each circle i, returns [left, right, bottom, top] slacks."""
    x = centers[:, 0]
    y = centers[:, 1]
    return np.column_stack([x - radii, 1.0 - x - radii, y - radii, 1.0 - y - radii])


def feasibility_projection(centers: np.ndarray, radii: np.ndarray, eps: float = 1e-12) -> Tuple[np.ndarray, np.ndarray]:
    """Project to satisfy boundary constraints: clamp centers in [0,1], shrink radii to boundary bounds if needed."""
    centers = clamp01(centers)
    u = boundary_upper_bounds(centers)
    radii = np.minimum(radii, np.maximum(0.0, u - eps))
    return centers, radii


def resolve_overlaps(
    centers: np.ndarray,
    radii: np.ndarray,
    eps: float = 1e-9,
    max_iter: int = 300,
    step: float = 0.25,
) -> Tuple[np.ndarray, np.ndarray]:
    """Resolve overlaps by moving centers apart slightly, while keeping inside [0,1].
    Does not change radii except for boundary projection (very small).
    """
    n = centers.shape[0]
    for _ in range(max_iter):
        moved = False
        d = pairwise_distances(centers)
        diff = centers[:, None, :] - centers[None, :, :]

        # Push away from neighbors where overlap exists
        for i in range(n):
            force = np.array([0.0, 0.0], dtype=float)
            for j in range(n):
                if i == j:
                    continue
                dij = d[i, j]
                target = radii[i] + radii[j] + eps
                overlap = target - dij
                if overlap > 0.0:
                    moved = True
                    # Direction away from j
                    if dij > 1e-12:
                        dir_vec = diff[i, j] / dij
                    else:
                        # Same position: pick a random small direction
                        ang = (i * 37 + j * 101) % 360
                        dir_vec = np.array([math.cos(math.radians(ang)), math.sin(math.radians(ang))])
                    # Repulsive force proportional to overlap
                    force += dir_vec * overlap
            # Boundary repulsion if near/trespass
            slack = compute_boundary_slacks(centers[[i]], radii[[i]])[0]
            # If violating, push back harder
            if slack[0] < 0:  # left
                force[0] += -slack[0] * 2.0
            if slack[1] < 0:  # right
                force[0] += slack[1] * -2.0
            if slack[2] < 0:  # bottom
                force[1] += -slack[2] * 2.0
            if slack[3] < 0:  # top
                force[1] += slack[3] * -2.0

            if np.any(force != 0.0):
                centers[i] += step * force

        if not moved:
            break
        centers = clamp01(centers)
        centers, radii = feasibility_projection(centers, radii, eps=eps)
    return centers, radii


# =====================
# Hexagonal seed design
# =====================

def hex_seed(pattern: List[int], s: float) -> np.ndarray:
    """Generate centers from a true hexagonal lattice with given row counts pattern and spacing s."""
    # Vertical spacing for hex (triangular) lattice
    v = s * math.sqrt(3.0) / 2.0
    R = len(pattern)
    # Center rows vertically
    total_h = v * (R - 1) if R > 1 else 0.0
    y0 = 0.5 - total_h / 2.0
    centers = []
    for r in range(R):
        cnt = pattern[r]
        # Horizontal positions centered around 0.5
        total_w = s * (cnt - 1) if cnt > 1 else 0.0
        x0 = 0.5 - total_w / 2.0
        y = y0 + r * v
        # Stagger alternate rows by s/2
        x_shift = (s / 2.0) if (r % 2 == 1) else 0.0
        for k in range(cnt):
            x = x0 + k * s + x_shift
            centers.append([x, y])
    centers = np.array(centers, dtype=float)
    # Clamp into the unit square to be safe
    return clamp01(centers)


# =====================
# LP inflation routines
# =====================

def linprog_fixed_centers(
    centers: np.ndarray,
    eps: float = 1e-9,
) -> Optional[np.ndarray]:
    """Solve fixed-center LP: maximize sum r subject to:
       0 <= r_i <= u_i  (boundary upper bounds)
       r_i + r_j <= d_ij - eps
       Returns radii (np.ndarray) or None if SciPy not available.
    """
    if not HAVE_SCIPY:
        return None

    n = centers.shape(0) if callable(getattr(centers, "shape", None)) else centers.shape[0]  # robust shape
    d = pairwise_distances(centers)
    u = boundary_upper_bounds(centers)
    # Handle degenerate cases: if u_i < 0, set to 0
    u = np.maximum(0.0, u)
    # Objective: maximize sum r -> minimize -sum r
    c = -np.ones(n)

    # Build inequalities A_ub * r <= b_ub
    rows = []
    rhs = []
    # Upper bounds r_i <= u_i
    for i in range(n):
        row = np.zeros(n)
        row[i] = 1.0
        rows.append(row)
        rhs.append(u[i])
    # Pairwise constraints r_i + r_j <= d_ij - eps
    for i in range(n):
        for j in range(i + 1, n):
            rhs_ij = d[i, j] - eps
            if rhs_ij < 0.0:
                rhs_ij = 0.0
            row = np.zeros(n)
            row[i] = 1.0
            row[j] = 1.0
            rows.append(row)
            rhs.append(rhs_ij)

    A_ub = np.array(rows, dtype=float)
    b_ub = np.array(rhs, dtype=float)
    bounds = [(0.0, None) for _ in range(n)]

    try:
        res = scipy_linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        if res.success and res.x is not None:
            r = np.array(res.x, dtype=float)
            # Numerical clean-up
            r = np.maximum(0.0, r)
            r = np.minimum(r, u)
            return r
        return None
    except Exception:
        return None


def greedy_inflate_fixed_centers(
    centers: np.ndarray,
    eps: float = 1e-9,
    passes: int = 120,
) -> np.ndarray:
    """Greedy fixed-center inflation without SciPy.
    Iteratively increases radii by consuming available boundary and pairwise slack.
    Deterministic and fast, not exact LP but good approximation for small n.
    """
    n = centers.shape[0]
    r = np.zeros(n, dtype=float)
    u = boundary_upper_bounds(centers)
    d = pairwise_distances(centers)

    # Precompute pair indices
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]

    for _ in range(passes):
        improved = False
        # Order by available slack descending
        boundary_slack = u - r
        pair_slack_min = np.full(n, np.inf)
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                s_ij = d[i, j] - eps - (r[i] + r[j])
                pair_slack_min[i] = min(pair_slack_min[i], s_ij)
        avail = np.maximum(0.0, np.minimum(boundary_slack, pair_slack_min))
        order = np.argsort(-avail)
        for i in order:
            inc = avail[i]
            if inc > 0.0:
                r[i] += inc
                improved = True
        if not improved:
            break
    # Final clipping for safety
    r = np.minimum(r, u)
    r = np.maximum(0.0, r)
    # Ensure pairwise feasibility by a final small safety shrink on offending pairs
    for (i, j) in pairs:
        s_ij = d[i, j] - eps - (r[i] + r[j])
        if s_ij < 0:
            # Reduce both slightly to satisfy
            delta = -s_ij / 2.0
            r[i] = max(0.0, r[i] - delta)
            r[j] = max(0.0, r[j] - delta)
    return r


def lp_inflate(centers: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    """Fixed-center inflation: try exact LP via SciPy, else greedy fallback."""
    if HAVE_SCIPY:
        r = linprog_fixed_centers(centers, eps=eps)
        if r is not None:
            return r
    # Fallback
    return greedy_inflate_fixed_centers(centers, eps=eps)


# ==================
# Growth and movement
# ==================

def growth_and_move(
    centers: np.ndarray,
    radii: np.ndarray,
    total_iters: int = 400,
    beta: float = 0.4,
    near_thresh: float = 0.03,
    trust_init: float = 0.01,
    trust_min: float = 1e-4,
    trust_max: float = 0.05,
    block: int = 50,
    eps: float = 1e-9,
) -> Tuple[np.ndarray, np.ndarray]:
    """Iteratively grow radii and move centers with adaptive trust region.
    Periodically apply fixed-center LP inflation; accept only if Σr increases.
    """
    n = centers.shape[0]
    best_centers = centers.copy()
    best_r = radii.copy()
    best_sum = float(np.sum(best_r))

    trust = trust_init
    last_block_sum = best_sum

    for it in range(1, total_iters + 1):
        # 1) Grow radii by consuming a fraction of available slack
        d = pairwise_distances(centers)
        boundary_slack = boundary_upper_bounds(centers) - radii
        pair_slack_min = np.full(n, np.inf)
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                s_ij = d[i, j] - eps - (radii[i] + radii[j])
                if s_ij < pair_slack_min[i]:
                    pair_slack_min[i] = s_ij
        avail = np.maximum(0.0, np.minimum(boundary_slack, pair_slack_min))
        order = np.argsort(-avail)
        for i in order:
            inc = beta * avail[i]
            if inc > 0.0:
                radii[i] += inc

        # 2) Gentle center moves driven by near constraints
        forces = np.zeros_like(centers)
        # Pairwise forces
        s_mat = d - (radii[:, None] + radii[None, :]) - eps
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                s_ij = s_mat[i, j]
                if s_ij < near_thresh:
                    # Push away from j
                    vec = centers[i] - centers[j]
                    dij = d[i, j]
                    if dij > 1e-12:
                        dir_vec = vec / dij
                    else:
                        # Degenerate direction
                        ang = (i * 97 + j * 131) % 360
                        dir_vec = np.array([math.cos(math.radians(ang)), math.sin(math.radians(ang))])
                    weight = (near_thresh - s_ij) / max(near_thresh, 1e-6)
                    forces[i] += dir_vec * weight
        # Boundary forces
        bsl = compute_boundary_slacks(centers, radii)
        # left, right, bottom, top
        for i in range(n):
            # push +x if near left
            if bsl[i, 0] < near_thresh:
                forces[i, 0] += (near_thresh - bsl[i, 0]) / max(near_thresh, 1e-6)
            # push -x if near right
            if bsl[i, 1] < near_thresh:
                forces[i, 0] -= (near_thresh - bsl[i, 1]) / max(near_thresh, 1e-6)
            # push +y if near bottom
            if bsl[i, 2] < near_thresh:
                forces[i, 1] += (near_thresh - bsl[i, 2]) / max(near_thresh, 1e-6)
            # push -y if near top
            if bsl[i, 3] < near_thresh:
                forces[i, 1] -= (near_thresh - bsl[i, 3]) / max(near_thresh, 1e-6)

        # Apply move
        centers += trust * forces
        centers = clamp01(centers)
        centers, radii = feasibility_projection(centers, radii, eps=eps)
        centers, radii = resolve_overlaps(centers, radii, eps=eps, max_iter=8, step=0.2)

        # 3) Periodic exact LP inflation on fixed centers
        if it % block == 0:
            r_lp = lp_inflate(centers, eps=eps)
            sum_lp = float(np.sum(r_lp))
            if sum_lp > best_sum + 1e-12:
                radii = r_lp
                best_centers = centers.copy()
                best_r = radii.copy()
                best_sum = sum_lp

            # Trust region adaptation
            if best_sum <= last_block_sum + 1e-12:
                trust = max(trust_min, trust * 0.7)
            else:
                trust = min(trust_max, trust * 1.1)
            last_block_sum = best_sum

    # Return best encountered
    return best_centers, best_r


# ==========================
# Global uniform inflation
# ==========================

def global_uniform_inflation(
    centers: np.ndarray,
    radii: np.ndarray,
    eps: float = 1e-9,
) -> Tuple[np.ndarray, np.ndarray]:
    """Uniformly scale (x, y, r) about (0.5, 0.5) to saturate a boundary without changing pairwise tangency ratios.
    Monotone acceptance is done outside.
    """
    c0 = np.array([0.5, 0.5], dtype=float)
    s = centers - c0
    x = s[:, 0]
    y = s[:, 1]
    r = radii.copy()

    # Determine maximum scale a >= 1 such that boundary constraints hold:
    # For each circle, constraints:
    # a*(r - x) <= 0.5
    # a*(r + x) <= 0.5
    # a*(r - y) <= 0.5
    # a*(r + y) <= 0.5
    # Only consider terms with positive multipliers; otherwise inequality is non-restrictive for a>=1.
    a_candidates = [1.0]
    for i in range(len(r)):
        terms = [
            r[i] - x[i],
            r[i] + x[i],
            r[i] - y[i],
            r[i] + y[i],
        ]
        for t in terms:
            if t > 1e-18:
                a_candidates.append(0.5 / t)
    a_max = min(a_candidates)
    a_max = max(1.0, a_max)
    # Apply scale slightly under the max to ensure strict feasibility
    a = max(1.0, a_max * (1.0 - 1e-12))

    centers2 = c0 + a * s
    radii2 = a * r
    centers2, radii2 = feasibility_projection(centers2, radii2, eps=eps)
    # No need to resolve overlaps: pairwise constraints are scaled equally
    return centers2, radii2


# ===========================
# Mini-barrier polish routine
# ===========================

def mini_barrier_polish(
    centers: np.ndarray,
    radii: np.ndarray,
    mu_schedule: List[float] = [5e-4, 2e-4, 1e-4],
    inner_steps: int = 40,
    eps: float = 1e-9,
) -> np.ndarray:
    """Keep radii fixed, perform short barrier ascent moving centers to increase slack:
    maximize sum log(boundary slacks) + sum log(pairwise slacks).
    Returns new centers.
    """
    n = centers.shape[0]
    C = centers.copy()

    for mu in mu_schedule:
        step = 0.02
        for _ in range(inner_steps):
            # Compute slacks
            d = pairwise_distances(C)
            s_pairs = d - (radii[:, None] + radii[None, :]) - eps
            bsl = compute_boundary_slacks(C, radii)
            if np.any(s_pairs <= 0.0) or np.any(bsl <= 0.0):
                # infeasible — shrink step and continue
                step *= 0.5
                if step < 1e-6:
                    break
                continue

            # Gradient of sum log slacks wrt centers
            grad = np.zeros_like(C)
            # Pairwise: for i<j, grad_i += (1/s_ij) * d(d_ij)/dx_i = (1/s_ij) * (x_i-x_j)/d_ij
            for i in range(n):
                for j in range(n):
                    if i == j:
                        continue
                    s_ij = s_pairs[i, j]
                    if s_ij <= 0:
                        continue
                    dij = d[i, j]
                    if dij <= 1e-12:
                        continue
                    dir_vec = (C[i] - C[j]) / dij
                    grad[i] += dir_vec * (1.0 / s_ij)

            # Boundary logs: log(x - r), log(1 - x - r), log(y - r), log(1 - y - r)
            # Gradients are +1/(x - r) for x, -1/(1 - x - r) for x, similarly for y
            x = C[:, 0]
            y = C[:, 1]
            # Avoid division by zero by clamping denominators (should be positive already)
            grad[:, 0] += 1.0 / np.maximum(bsl[:, 0], 1e-18)  # left
            grad[:, 0] += -1.0 / np.maximum(bsl[:, 1], 1e-18)  # right
            grad[:, 1] += 1.0 / np.maximum(bsl[:, 2], 1e-18)  # bottom
            grad[:, 1] += -1.0 / np.maximum(bsl[:, 3], 1e-18)  # top

            # Scale by mu
            grad *= mu

            # Backtracking line search to keep feasibility
            ok = False
            for _bt in range(10):
                C_try = C + step * grad
                C_try = clamp01(C_try)
                bsl_try = compute_boundary_slacks(C_try, radii)
                d_try = pairwise_distances(C_try)
                s_pairs_try = d_try - (radii[:, None] + radii[None, :]) - eps
                if np.all(bsl_try > 0.0) and np.all(s_pairs_try > 0.0):
                    C = C_try
                    ok = True
                    break
                step *= 0.5
            if not ok:
                # no feasible move
                break

    return C


# =====================
# Seed running pipeline
# =====================

def run_seed(centers: np.ndarray, eps: float = 1e-9) -> Tuple[np.ndarray, np.ndarray]:
    """Run the full TriHex++ pipeline from an initial center seed."""
    n = centers.shape[0]
    # Initialization: LP bootstrap (fixed centers)
    r = lp_inflate(centers, eps=eps)
    centers, r = feasibility_projection(centers, r, eps=eps)

    best_centers = centers.copy()
    best_r = r.copy()
    best_sum = float(np.sum(best_r))

    # Growth-and-move with periodic LP
    centers2, r2 = growth_and_move(centers.copy(), r.copy(), eps=eps)
    if np.sum(r2) > best_sum + 1e-12:
        best_centers, best_r = centers2, r2
        best_sum = float(np.sum(best_r))

    # Global uniform inflation and follow-up LP
    c_g, r_g = global_uniform_inflation(best_centers, best_r, eps=eps)
    r_lp = lp_inflate(c_g, eps=eps)
    if np.sum(r_lp) > best_sum + 1e-12:
        best_centers, best_r = c_g, r_lp
        best_sum = float(np.sum(best_r))

    # Optional second uniform pass
    c_g2, r_g2 = global_uniform_inflation(best_centers, best_r, eps=eps)
    r_lp2 = lp_inflate(c_g2, eps=eps)
    if np.sum(r_lp2) > best_sum + 1e-12:
        best_centers, best_r = c_g2, r_lp2
        best_sum = float(np.sum(best_r))

    # LP–mini-barrier–LP sandwich polish
    r_lp3 = lp_inflate(best_centers, eps=eps)
    if np.sum(r_lp3) > best_sum + 1e-12:
        best_r = r_lp3
        best_sum = float(np.sum(best_r))

    C_pol = mini_barrier_polish(best_centers.copy(), best_r.copy(), eps=eps)
    r_pol = lp_inflate(C_pol, eps=eps)
    if np.sum(r_pol) > best_sum + 1e-12:
        best_centers, best_r = C_pol, r_pol
        best_sum = float(np.sum(best_r))

    # Final feasibility and tiny shrink
    best_centers, best_r = feasibility_projection(best_centers, best_r, eps=eps)
    best_centers, best_r = resolve_overlaps(best_centers, best_r, eps=eps, max_iter=30, step=0.2)
    best_r *= (1.0 - 1e-9)

    return best_centers, best_r


# ====================
# Multi-start strategy
# ====================

def construct_packing(num_circles: int = 21):
    """Main entry: construct 21 disjoint circles inside the unit square maximizing sum of radii.

    Returns:
        np.ndarray of shape (n, 3): rows [x, y, r]
    """
    assert num_circles == 21, "This solver is tuned for exactly 21 circles."

    # Patterns: five-row counts summing to 21
    patterns = [
        [5, 4, 5, 4, 3],
        [5, 5, 4, 4, 3],
        [4, 5, 5, 4, 3],
        [5, 4, 4, 4, 4],
        [4, 5, 4, 4, 4],
        [4, 4, 5, 4, 4],
        [4, 4, 4, 5, 4],
    ]
    # Lattice spacings to explore around ~0.2
    spacings = [0.2 * f for f in [0.96, 0.98, 1.00, 1.02, 1.04]]

    rng = np.random.default_rng(123456)  # deterministic for reproducibility
    jitter_scale = 0.002
    per_combo_keep = 2  # K survivors per (pattern, spacing)

    survivors: List[Tuple[float, np.ndarray]] = []
    for pat in patterns:
        for s in spacings:
            # Generate a base seed
            base = hex_seed(pat, s)
            # If more than 21 due to pattern mismatch, trim; if fewer, pad with random points
            if base.shape[0] > num_circles:
                base = base[:num_circles]
            elif base.shape[0] < num_circles:
                # pad random points near center
                extra = num_circles - base.shape[0]
                extra_pts = rng.uniform(0.35, 0.65, size=(extra, 2))
                base = np.vstack([base, extra_pts])

            # Generate jittered variants
            candidates: List[Tuple[float, np.ndarray]] = []
            for _ in range(8):
                jitter = rng.uniform(-jitter_scale, jitter_scale, size=base.shape)
                seed = clamp01(base + jitter)
                # LP-only preselection on fixed centers
                r_lp = lp_inflate(seed, eps=1e-9)
                sum_r = float(np.sum(r_lp))
                candidates.append((sum_r, seed))

            # Keep top-K per combo
            candidates.sort(key=lambda t: t[0], reverse=True)
            survivors.extend(candidates[:per_combo_keep])

    # Sort all survivors by Σr and keep the best M to run the heavy pipeline
    survivors.sort(key=lambda t: t[0], reverse=True)
    top_M = min(12, len(survivors))
    survivors = survivors[:top_M]

    # Run heavy pipeline and keep the best result
    best_sum = -1.0
    best_centers = None
    best_r = None

    for pre_sum, seed in survivors:
        centers, radii = run_seed(seed, eps=1e-9)
        ssum = float(np.sum(radii))
        if ssum > best_sum + 1e-12:
            best_sum = ssum
            best_centers = centers
            best_r = radii

    # As a fallback (shouldn't happen), if survivors empty or best is None, default to simple grid
    if best_centers is None or best_r is None:
        radius = 0.099999
        centers = np.array(
            [[(column + 0.5) / 5, (row + 0.5) / 5] for row in range(5) for column in range(5)][:num_circles],
            dtype=float,
        )
        radii = np.full(num_circles, radius, dtype=float)
        circles = np.column_stack((centers, radii))
        return circles

    circles = np.column_stack((best_centers, best_r))
    # Final safeguard: ensure strict feasibility and shape (21,3)
    circles[:, :2] = clamp01(circles[:, :2])
    ub = boundary_upper_bounds(circles[:, :2])
    circles[:, 2] = np.minimum(circles[:, 2], ub * (1.0 - 1e-12))
    # Return exactly 21 circles
    if circles.shape[0] > num_circles:
        circles = circles[:num_circles]
    return circles


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```

```python
#!/usr/bin/env python3
"""Improved candidate for packing 21 circles in a perimeter-four rectangle.

Implements the discrete perimeter-balanced two-scale construction with polishing:

Core components:
- Discrete perimeter-balanced search for the best m_x × m_y grid and interstitial count t.
- Two-scale seed geometry: base circles arranged on a rectangular grid, plus up to t
  interstitial circles placed at cell centers.
- Clearance-aware interstitial selection with deterministic tie-breaking; multi-start seeds.
- Optional nonlinear polishing (if SciPy is available) that optimizes centers, radii,
  and rectangle sides W, H under:
    • non-overlap,
    • boundary containment,
    • perimeter budget W + H = 2 (active at optimum).
  Uses sparse adjacency (grid/knn/interstitial neighbors) and lazy constraint augmentation,
  plus epsilon annealing from 1e−6 → 1e−12.
- Final uniform shrink δ = 1e−12 for strict disjointness.

Mutation (EVOLVE): A final LP–mini-barrier–LP sandwich step is added as an endgame
refinement. It reuses the fixed-center LP and adds a short interior-barrier ascent
on (x, y, r) with fixed W, H to create slack, with strict acceptance gates.

The result is 21 disjoint circles inside an axis-aligned rectangle with perimeter = 4,
with a strong sum of radii for N = 21.
"""

import json
from typing import List, Tuple, Optional, Dict, Set

import numpy as np


# EVOLVE_START
def _select_grid_for_N(N: int) -> Tuple[int, int, int]:
    """Enumerate feasible (m_x, m_y) grids and select the best by the score S.

    A grid (m_x, m_y) can host B = m_x*m_y base circles and up to
    I = (m_x - 1)*(m_y - 1) interstitials. For target N, set t = N - B and
    require 0 <= t <= I.

    Score:
      S = [B + α (N − B)] / (m_x + m_y), where α = sqrt(2) − 1.

    Tie-breakers:
      1) minimize (m_x + m_y)
      2) minimize |m_x − m_y|
      3) lexicographic (m_x, m_y)

    Returns:
      (m_x, m_y, t)
    """
    if N <= 0:
        return (1, 1, 0)

    alpha = np.sqrt(2.0) - 1.0

    best = None
    best_key = None

    # Enumerate reasonable ranges; for safety cap at N (sufficient).
    for mx in range(1, N + 1):
        for my in range(1, N + 1):
            B = mx * my
            if B > N:
                continue
            I = (mx - 1) * (my - 1) if mx > 0 and my > 0 else 0
            t = N - B
            if t < 0 or t > I:
                continue

            S = (B + alpha * (N - B)) / (mx + my)

            # Build sorting key: maximize S -> minimize -S
            key = (-S, mx + my, abs(mx - my), mx, my)
            if best is None or key < best_key:
                best = (mx, my, t)
                best_key = key

    if best is None:
        # Fallback (should not happen with the above enumeration)
        best = (1, N, 0)

    return best


def _grid_seed_geometry(mx: int, my: int) -> Tuple[float, float, float, float]:
    """Return base geometry values for the grid:
    r0_exact, W, H, r1_exact such that W + H = 2 and r1_exact = (sqrt(2) - 1) * r0_exact.
    """
    r0_exact = 1.0 / float(mx + my)
    W = 2.0 * mx * r0_exact
    H = 2.0 * my * r0_exact
    r1_exact = r0_exact * (np.sqrt(2.0) - 1.0)
    return r0_exact, W, H, r1_exact


def _base_centers(mx: int, my: int, W: float, H: float) -> np.ndarray:
    """Compute m_x × m_y base centers on a regular grid."""
    xs = (np.arange(mx) + 0.5) * W / mx
    ys = (np.arange(my) + 0.5) * H / my
    centers = np.array([(x, y) for y in ys for x in xs], dtype=float)
    return centers  # shape (mx*my, 2)


def _cell_centers(mx: int, my: int, W: float, H: float) -> List[Tuple[int, int, float, float]]:
    """Return list of interstitial candidate cell centers with their cell indices (i, j)."""
    out = []
    for j in range(my - 1):
        for i in range(mx - 1):
            x = (i + 1) * W / mx
            y = (j + 1) * H / my
            out.append((i, j, x, y))
    return out


def _clearance_proxy_for_cell(i: int, j: int, mx: int, my: int, W: float, H: float, r0: float) -> float:
    """Compute a local clearance proxy for the interstitial at cell (i, j).

    Proxy is the minimum of:
      - distance to any of the four neighboring base centers minus r0
      - distance to the boundary of the rectangle
    The distances to the four base centers are identical for cell centers in a
    regular grid, but we compute the formula explicitly for clarity.
    """
    # Interstitial center
    x = (i + 1) * W / mx
    y = (j + 1) * H / my
    # Neighbor base centers at the corners of the cell
    cx = [(i + 0.5) * W / mx, (i + 1.5) * W / mx]
    cy = [(j + 0.5) * H / my, (j + 1.5) * H / my]
    dmin_to_base = float("inf")
    for xb in cx:
        for yb in cy:
            d = np.hypot(x - xb, y - yb) - r0
            if d < dmin_to_base:
                dmin_to_base = d
    # Distance to boundary
    d_boundary = min(x, W - x, y, H - y)
    return min(dmin_to_base, d_boundary)


def _interstitial_candidates_ranked(mx: int, my: int, W: float, H: float, r0: float) -> List[Tuple[float, float, int, int, float, float]]:
    """Return ranked interstitial candidates:
    Each item is (score_desc, tie_center_dist2, j, i, x, y).
    We will sort by highest clearance score, then closest to center, then by (j, i).
    """
    cx, cy = W / 2.0, H / 2.0
    ranked: List[Tuple[float, float, int, int, float, float]] = []
    for j in range(my - 1):
        for i in range(mx - 1):
            x = (i + 1) * W / mx
            y = (j + 1) * H / my
            score = _clearance_proxy_for_cell(i, j, mx, my, W, H, r0)
            d2 = (x - cx) ** 2 + (y - cy) ** 2
            ranked.append((score, -d2, j, i, x, y))
    # Sort descending by score (hence reverse), then ascending by distance (since we used -d2),
    # then by j, i for determinism.
    ranked.sort(key=lambda it: (-it[0], it[1], it[2], it[3]))
    return ranked


def _build_initial_circles(mx: int, my: int, t: int, eps0: float, multistart_k: int = 5) -> List[Dict]:
    """Build one or more seed layouts.

    Returns a list of seeds (dicts) with keys:
      - 'W', 'H', 'r0_exact', 'r1_exact', 'circles' (array Nx3), 'types' (list of 'base'/'inter'),
        'meta' (dictionary with indices and mapping details for adjacency),
        'interstitial_cells' (list of chosen cell (i, j))
    If t == 1, we produce up to multistart_k seeds by picking the top-k ranked candidates.
    Otherwise we produce a single seed with the top-t ranked interstitials.
    """
    seeds: List[Dict] = []

    r0_exact, W, H, r1_exact = _grid_seed_geometry(mx, my)
    r0 = max(0.0, r0_exact - eps0)
    r1 = max(0.0, r1_exact - eps0)

    base_centers = _base_centers(mx, my, W, H)
    ranked = _interstitial_candidates_ranked(mx, my, W, H, r0_exact)

    # Utility to build one seed given chosen cells
    def make_seed(chosen_cells: List[Tuple[int, int]]) -> Dict:
        circles: List[Tuple[float, float, float]] = []
        types: List[str] = []
        # Base circles first
        for (x, y) in base_centers:
            circles.append((x, y, r0))
            types.append("base")
        # Interstitials
        for (ci, cj) in chosen_cells:
            x = (ci + 1) * W / mx
            y = (cj + 1) * H / my
            circles.append((x, y, r1))
            types.append("inter")

        circles_arr = np.array(circles, dtype=float)

        # Build meta for adjacency
        # Map base (i,j) -> index
        base_index = {(i, j): (j * mx + i) for j in range(my) for i in range(mx)}
        # Interstitial map: for each chosen cell (ci, cj), record its circle index and its four base neighbors
        inter_map = {}
        for k, (ci, cj) in enumerate(chosen_cells):
            idx = len(base_centers) + k
            neighbors = [(ci, cj), (ci + 1, cj), (ci, cj + 1), (ci + 1, cj + 1)]
            inter_map[(ci, cj)] = {
                "index": idx,
                "base_neighbors": [base_index[p] for p in neighbors],
            }

        meta = {
            "mx": mx,
            "my": my,
            "base_index": base_index,
            "inter_map": inter_map,
            "B": mx * my,
            "t": len(chosen_cells),
        }
        return {
            "W": W,
            "H": H,
            "r0_exact": r0_exact,
            "r1_exact": r1_exact,
            "circles": circles_arr,
            "types": types,
            "meta": meta,
            "interstitial_cells": chosen_cells,
        }

    if t <= 0:
        seeds.append(make_seed([]))
        return seeds

    # If only one interstitial, build multiple seeds (multi-start) from top-k candidates
    if t == 1:
        k = min(multistart_k, len(ranked))
        for h in range(k):
            _, _, j, i, _, _ = ranked[h]
            seeds.append(make_seed([(i, j)]))
    else:
        # Choose top-t candidates deterministically
        chosen: List[Tuple[int, int]] = []
        for h in range(min(t, len(ranked))):
            _, _, j, i, _, _ = ranked[h]
            chosen.append((i, j))
        seeds.append(make_seed(chosen))

    return seeds


def _build_initial_adjacency(seed: Dict) -> Set[Tuple[int, int]]:
    """Build a sparse initial adjacency set for non-overlap constraints.

    Includes:
      - Grid neighbors (4-neighborhood and diagonals) among base circles.
      - Interstitial circle to its four surrounding base circles.
      - k-nearest neighbors (k=6) to capture nearby contacts.
    """
    circles = seed["circles"]
    meta = seed["meta"]
    mx = meta["mx"]
    my = meta["my"]
    B = meta["B"]
    t = meta["t"]

    N = circles.shape[0]
    centers = circles[:, :2]

    pairs: Set[Tuple[int, int]] = set()

    # Base grid neighbors (4-neighborhood + diagonals)
    def idx(i: int, j: int) -> int:
        return j * mx + i

    for j in range(my):
        for i in range(mx):
            a = idx(i, j)
            # Right/left
            if i + 1 < mx:
                b = idx(i + 1, j)
                pairs.add((min(a, b), max(a, b)))
            if j + 1 < my:
                b = idx(i, j + 1)
                pairs.add((min(a, b), max(a, b)))
            # Diagonals
            if i + 1 < mx and j + 1 < my:
                b = idx(i + 1, j + 1)
                pairs.add((min(a, b), max(a, b)))
            if i - 1 >= 0 and j + 1 < my:
                b = idx(i - 1, j + 1)
                pairs.add((min(a, b), max(a, b)))

    # Interstitial neighbors to their four surrounding base circles
    for (ci, cj), imap in meta["inter_map"].items():
        inter_idx = imap["index"]
        for bidx in imap["base_neighbors"]:
            pairs.add((min(inter_idx, bidx), max(inter_idx, bidx)))

    # k-nearest neighbors to capture nearby pairs
    k = 6
    for i in range(N):
        d2 = np.sum((centers - centers[i]) ** 2, axis=1)
        order = np.argsort(d2)
        cnt = 0
        for j in order:
            if j == i:
                continue
            pairs.add((min(i, j), max(i, j)))
            cnt += 1
            if cnt >= k:
                break

    return pairs


def _build_sparse_adjacency_centers(centers: np.ndarray, k: int = 6) -> Set[Tuple[int, int]]:
    """Build a sparse adjacency directly from centers using k-nearest neighbors."""
    N = centers.shape[0]
    pairs: Set[Tuple[int, int]] = set()
    for i in range(N):
        d2 = np.sum((centers - centers[i]) ** 2, axis=1)
        order = np.argsort(d2)
        cnt = 0
        for j in order:
            if j == i:
                continue
            pairs.add((min(i, j), max(i, j)))
            cnt += 1
            if cnt >= k:
                break
    return pairs


def _lp_inflate_fixed_centers(centers: np.ndarray, W: float, H: float, eps: float) -> Tuple[Optional[np.ndarray], bool]:
    """Fixed-center linear program to maximize sum of radii.

    Given fixed centers and rectangle (W, H), solve:
      maximize sum r_i
      subject to:
        0 <= r_i <= min(x_i, W - x_i, y_i, H - y_i) - eps
        r_i + r_j <= ||c_i - c_j|| - eps  for all i < j

    Implemented as minimize -sum r_i, using SciPy linprog (method='highs').
    Returns (radii, True) if successful, else (None, False).
    """
    try:
        from scipy.optimize import linprog
    except Exception:
        return None, False

    N = centers.shape[0]
    # Bounds per variable
    x = centers[:, 0]
    y = centers[:, 1]
    ub = np.minimum.reduce([x, W - x, y, H - y]) - eps
    ub = np.clip(ub, 0.0, None)
    bounds = [(0.0, float(ub[i])) for i in range(N)]

    # Pairwise constraints A_ub r <= b_ub
    # For each pair: (e_i + e_j) r <= d_ij - eps
    pairs_i = []
    pairs_j = []
    b = []
    for i in range(N):
        for j in range(i + 1, N):
            dx = centers[i, 0] - centers[j, 0]
            dy = centers[i, 1] - centers[j, 1]
            dij = float(np.hypot(dx, dy))
            rhs = dij - eps
            pairs_i.append(i)
            pairs_j.append(j)
            b.append(rhs)

    # Build sparse-like A as dense since N is small (<= 21)
    m = len(b)
    if m > 0:
        A = np.zeros((m, N), dtype=float)
        for k, (i, j) in enumerate(zip(pairs_i, pairs_j)):
            A[k, i] = 1.0
            A[k, j] = 1.0
        A_ub = A
        b_ub = np.array(b, dtype=float)
    else:
        A_ub = None
        b_ub = None

    # Objective: minimize -sum r_i
    c = -np.ones(N, dtype=float)

    res = linprog(c=c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
    if not res.success or res.x is None:
        return None, False

    r = np.array(res.x, dtype=float)
    return r, True


def _centered_uniform_inflation(W: float, H: float, centers: np.ndarray, radii: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float, bool]:
    """Compute and apply a centered uniform inflation (about rectangle center).

    Scaling:
      Let (cx, cy) = (W/2, H/2). For t >= 1,
        x' = cx + t (x - cx), y' = cy + t (y - cy), r' = t r.

    Pairwise separations and radii both scale by t, so non-overlap is preserved.
    To maintain boundary containment we require, for each circle:
      x' - r' >= 0
      W - (x' + r') >= 0
      y' - r' >= 0
      H - (y' + r') >= 0

    This yields linear inequalities a + b t >= 0. We find the largest feasible t >= 1.

    Returns (centers', radii', t, applied_flag).
    """
    N = centers.shape[0]
    cx, cy = W / 2.0, H / 2.0
    x = centers[:, 0]
    y = centers[:, 1]
    r = radii

    # Collect bounds on t: lower (from b>0), upper (from b<0)
    t_lower = 1.0
    t_upper = float("inf")

    # For each circle and each boundary inequality
    for i in range(N):
        # Left: cx + t*(x - cx - r) >= 0
        a = cx
        b = (x[i] - cx - r[i])
        if b > 0:
            t_lower = max(t_lower, -a / b)
        elif b < 0:
            t_upper = min(t_upper, -a / b)
        else:
            pass

        # Right: W - cx - t*(x - cx + r) >= 0
        a = W - cx
        b = -(x[i] - cx + r[i])
        if b > 0:
            t_lower = max(t_lower, -a / b)
        elif b < 0:
            t_upper = min(t_upper, -a / b)

        # Bottom: cy + t*(y - cy - r) >= 0
        a = cy
        b = (y[i] - cy - r[i])
        if b > 0:
            t_lower = max(t_lower, -a / b)
        elif b < 0:
            t_upper = min(t_upper, -a / b)

        # Top: H - cy - t*(y - cy + r) >= 0
        a = H - cy
        b = -(y[i] - cy + r[i])
        if b > 0:
            t_lower = max(t_lower, -a / b)
        elif b < 0:
            t_upper = min(t_upper, -a / b)

    if not np.isfinite(t_upper):
        t_upper = float("inf")
    t_max = min(t_upper, max(t_lower, 1.0))

    if not (t_max > 1.0 + 1e-12):
        return centers.copy(), radii.copy(), 1.0, False

    t = t_max
    centers_new = np.empty_like(centers)
    centers_new[:, 0] = cx + t * (x - cx)
    centers_new[:, 1] = cy + t * (y - cy)
    radii_new = t * r

    return centers_new, radii_new, t, True


def _solve_polish(seed: Dict, eps_schedule: List[float], max_lazy_iters: int = 3) -> Optional[Dict]:
    """Polish a seed using constrained nonlinear optimization (SLSQP if available).

    Returns a dict with keys:
      - 'W', 'H', 'circles' (Nx3) for the best polished result,
      or None if polishing fails entirely.
    """
    try:
        from scipy.optimize import minimize
    except Exception:
        return None  # SciPy not available

    circles0 = seed["circles"].copy()
    W0 = seed["W"]
    H0 = seed["H"]
    N = circles0.shape[0]

    # Variable layout: v = [W, H, x[0..N-1], y[0..N-1], r[0..N-1]]
    def pack_vars(W, H, centers, radii):
        return np.concatenate(([W, H], centers[:, 0], centers[:, 1], radii))

    def unpack_vars(v):
        W, H = v[0], v[1]
        x = v[2 : 2 + N]
        y = v[2 + N : 2 + 2 * N]
        r = v[2 + 2 * N : 2 + 3 * N]
        centers = np.stack([x, y], axis=1)
        return W, H, centers, r

    # Initial variables
    v0 = pack_vars(W0, H0, circles0[:, :2], circles0[:, 2])

    # Anchor circle: bottom-left among base circles (min y then min x)
    meta = seed["meta"]
    B = meta["B"]
    base_centers = circles0[:B, :2]
    base_order = np.lexsort((base_centers[:, 0], base_centers[:, 1]))  # sort by y, then x
    anchor_idx = int(base_order[0])

    # Build an initial adjacency
    adjacency = _build_initial_adjacency(seed)

    best_result = None
    best_sum_r = -np.inf

    # To support the final centered uniform inflation, keep track of the last accepted (W,H,centers,r)
    last_WHR = None  # tuple (W, H, centers, r)

    # Epsilon annealing loop
    for eps in eps_schedule:
        lazy_iters = 0
        adjacency_current = set(adjacency)

        while lazy_iters <= max_lazy_iters:
            # Build constraints for given eps and adjacency
            cons = []

            # Equality: perimeter W + H = 2
            def ceq_perimeter(v):
                W, H, _, _ = unpack_vars(v)
                return 2.0 - (W + H)

            cons.append({"type": "eq", "fun": ceq_perimeter})

            # Inequalities: W >= 0, H >= 0
            cons.append({"type": "ineq", "fun": lambda v: v[0]})
            cons.append({"type": "ineq", "fun": lambda v: v[1]})

            # Anchor equalities for the bottom-left base circle: x - r = 0 and y - r = 0
            def ceq_anchor_x(v, idx=anchor_idx):
                W, H, centers, r = unpack_vars(v)
                return centers[idx, 0] - r[idx]

            def ceq_anchor_y(v, idx=anchor_idx):
                W, H, centers, r = unpack_vars(v)
                return centers[idx, 1] - r[idx]

            cons.append({"type": "eq", "fun": ceq_anchor_x})
            cons.append({"type": "eq", "fun": ceq_anchor_y})

            # Boundary inequalities for each circle:
            # x_i - r_i >= 0; W - r_i - x_i >= 0; y_i - r_i >= 0; H - r_i - y_i >= 0
            for i in range(N):
                cons.append({"type": "ineq", "fun": (lambda v, i=i: unpack_vars(v)[2][i, 0] - unpack_vars(v)[3][i])})
                cons.append(
                    {"type": "ineq", "fun": (lambda v, i=i: unpack_vars(v)[0] - unpack_vars(v)[3][i] - unpack_vars(v)[2][i, 0])}
                )
                cons.append({"type": "ineq", "fun": (lambda v, i=i: unpack_vars(v)[2][i, 1] - unpack_vars(v)[3][i])})
                cons.append(
                    {"type": "ineq", "fun": (lambda v, i=i: unpack_vars(v)[1] - unpack_vars(v)[3][i] - unpack_vars(v)[2][i, 1])}
                )
                # r_i >= 0
                cons.append({"type": "ineq", "fun": (lambda v, i=i: unpack_vars(v)[3][i])})

            # Non-overlap constraints for pairs in adjacency using squared separation
            for (i, j) in sorted(adjacency_current):
                def g_pair(v, i=i, j=j, eps=eps):
                    _, _, centers, r = unpack_vars(v)
                    dx = centers[i, 0] - centers[j, 0]
                    dy = centers[i, 1] - centers[j, 1]
                    d2 = dx * dx + dy * dy
                    rs = r[i] + r[j] + eps
                    return d2 - rs * rs

                cons.append({"type": "ineq", "fun": g_pair})

            def f(v):
                _, _, _, r = unpack_vars(v)
                return -np.sum(r)

            def fprime(v):
                grad = np.zeros_like(v)
                grad[2 + 2 * N :] = -1.0
                return grad

            res = minimize(
                f,
                v0,
                jac=fprime,
                constraints=cons,
                method="SLSQP",
                options={"maxiter": 400, "ftol": 1e-12, "disp": False},
            )

            if not res.success:
                v_res = res.x if isinstance(res.x, np.ndarray) else v0
            else:
                v_res = res.x

            v0 = v_res.copy()

            W, H, centers, r = unpack_vars(v_res)
            violated_pairs: List[Tuple[int, int, float]] = []
            for i in range(N):
                for j in range(i + 1, N):
                    dx = centers[i, 0] - centers[j, 0]
                    dy = centers[i, 1] - centers[j, 1]
                    d2 = dx * dx + dy * dy
                    rs = r[i] + r[j] + eps
                    if d2 < rs * rs - 1e-16:
                        violated_pairs.append((i, j, d2 - rs * rs))

            if violated_pairs:
                for (i, j, _) in violated_pairs:
                    adjacency_current.add((i, j))
                lazy_iters += 1
                continue  # re-solve with augmented adjacency
            else:
                sum_r = float(np.sum(r))

                # Post-annealing-stage LP inflation with acceptance gate
                r_lp, ok_lp = _lp_inflate_fixed_centers(centers, W, H, eps=eps)
                if ok_lp:
                    sum_r_lp = float(np.sum(r_lp))
                    if sum_r_lp > sum_r + 1e-15:
                        r = r_lp
                        sum_r = sum_r_lp
                        v0 = pack_vars(W, H, centers, r)

                if sum_r > best_sum_r:
                    best_sum_r = sum_r
                    best_result = {"W": W, "H": H, "circles": np.concatenate([centers, r[:, None]], axis=1)}
                last_WHR = (W, H, centers.copy(), r.copy())

                break  # move to next epsilon

    # After the final epsilon stage converges, attempt a centered uniform inflation + LP,
    # accepting only if it improves the sum of radii.
    if last_WHR is not None:
        W, H, centers, r = last_WHR
        centers_scaled, r_scaled, t, applied = _centered_uniform_inflation(W, H, centers, r)
        if applied:
            eps_final = eps_schedule[-1] if len(eps_schedule) > 0 else 1e-12
            r_lp, ok_lp = _lp_inflate_fixed_centers(centers_scaled, W, H, eps=eps_final)
            if ok_lp:
                sum_old = float(np.sum(r))
                sum_new = float(np.sum(r_lp))
                if sum_new > sum_old + 1e-15:
                    best_sum_r = sum_new
                    best_result = {
                        "W": W,
                        "H": H,
                        "circles": np.concatenate([centers_scaled, r_lp[:, None]], axis=1),
                    }

    return best_result


def _finalize_refine(state: Dict, eps_stage: float) -> Dict:
    """Final LP–mini-barrier–LP sandwich refinement.

    Steps:
      Optional) Centered uniform inflation hook (if not triggered earlier).
      A1) Fixed-center LP with eps_lp_final = min(eps_stage, 1e-10).
      A2) Mini-barrier ascent on (x, y, r) with W, H fixed (μ schedule with backtracking).
      A3) Fixed-center LP with eps_lp_final = 1e-12 to capture created slack.

    Acceptance gates: Each substep is applied only if sum of radii strictly increases.
    If SciPy is unavailable for LP, those substeps are skipped gracefully.
    """
    W = float(state["W"])
    H = float(state["H"])
    circles = state["circles"].copy()
    centers = circles[:, :2].copy()
    r = circles[:, 2].copy()
    N = r.size

    def sum_r(v: np.ndarray) -> float:
        return float(np.sum(v))

    # Optional uniform inflation hook before sandwich; then LP capture
    centers_u, r_u, t, applied = _centered_uniform_inflation(W, H, centers, r)
    if applied:
        r_lp, ok = _lp_inflate_fixed_centers(centers_u, W, H, eps=min(eps_stage, 1e-10))
        if ok and r_lp is not None:
            if sum_r(r_lp) > sum_r(r) + 1e-15:
                centers, r = centers_u, r_lp

    # A1: LP reallocate (warm start) at tighter eps
    r_lp1, ok1 = _lp_inflate_fixed_centers(centers, W, H, eps=min(eps_stage, 1e-10))
    if ok1 and r_lp1 is not None and sum_r(r_lp1) > sum_r(r) + 1e-15:
        r = r_lp1

    # A2: Mini-barrier polish (centers + radii), W,H fixed
    # Build a sparse adjacency (k-NN) and lazily augment
    adjacency = _build_sparse_adjacency_centers(centers, k=6)

    # Work copies; pre-barrier tiny shrink to guarantee strict slacks
    centers_mb = centers.copy()
    r_mb = (r * 0.999999).copy()

    # Feasibility epsilon and barrier clearance schedule
    eps_feas = float(eps_stage)
    eps_b0 = min(eps_feas, 1e-8)
    eps_b1 = 1e-12
    # Three μ values and corresponding barrier inner clearances (geometric interpolation)
    mu_schedule = [2e-3, 1e-3, 5e-4]
    eps_b_schedule = [
        eps_b0,
        float(np.sqrt(eps_b0 * eps_b1)),
        eps_b1,
    ]
    base_step = 0.02
    min_step = 1e-8
    backtrack = 0.5

    def compute_slacks(cent: np.ndarray, rad: np.ndarray, pairs: Set[Tuple[int, int]]) -> Dict[str, np.ndarray]:
        x = cent[:, 0]
        y = cent[:, 1]
        # Boundary slacks with feasibility epsilon
        sL = x - rad - eps_feas
        sR = W - x - rad - eps_feas
        sB = y - rad - eps_feas
        sT = H - y - rad - eps_feas
        sPos = rad - eps_feas
        # Pairwise slacks
        if len(pairs) > 0:
            idx_i = np.fromiter((i for (i, _) in pairs), dtype=int, count=len(pairs))
            idx_j = np.fromiter((j for (_, j) in pairs), dtype=int, count=len(pairs))
            dx = cent[idx_i, 0] - cent[idx_j, 0]
            dy = cent[idx_i, 1] - cent[idx_j, 1]
            dij = np.hypot(dx, dy)
            sP = dij - (rad[idx_i] + rad[idx_j]) - eps_feas
            return {
                "left": sL,
                "right": sR,
                "bottom": sB,
                "top": sT,
                "pos": sPos,
                "pair": sP,
                "pair_ij": (idx_i, idx_j, dx, dy, dij),
            }
        else:
            return {"left": sL, "right": sR, "bottom": sB, "top": sT, "pos": sPos, "pair": np.zeros(0), "pair_ij": None}

    def barrier_value_and_grad(cent: np.ndarray, rad: np.ndarray, pairs: Set[Tuple[int, int]], mu: float, eps_b: float) -> Tuple[float, np.ndarray, np.ndarray, np.ndarray]:
        # J = sum(rad) + mu * sum log(slack - eps_b)
        sl = compute_slacks(cent, rad, pairs)
        # Check for strict feasibility for barrier: all s - eps_b > 0
        for key in ("left", "right", "bottom", "top", "pos"):
            if np.any(sl[key] - eps_b <= 0.0):
                zeros = np.zeros_like(rad)
                return -np.inf, zeros, zeros, zeros
        if sl["pair"].size > 0 and np.any(sl["pair"] - eps_b <= 0.0):
            zeros = np.zeros_like(rad)
            return -np.inf, zeros, zeros, zeros

        # Barrier value
        J = float(np.sum(rad))
        J += mu * (
            np.sum(np.log(sl["left"] - eps_b))
            + np.sum(np.log(sl["right"] - eps_b))
            + np.sum(np.log(sl["bottom"] - eps_b))
            + np.sum(np.log(sl["top"] - eps_b))
            + np.sum(np.log(sl["pos"] - eps_b))
        )
        if sl["pair"].size > 0:
            J += mu * np.sum(np.log(sl["pair"] - eps_b))

        # Gradient accumulators
        gx = np.zeros(rad.shape[0], dtype=float)
        gy = np.zeros(rad.shape[0], dtype=float)
        gr = np.ones(rad.shape[0], dtype=float)  # from sum(rad)

        # Boundary grads: denom = 1/(s - eps_b)
        dL = mu / (sl["left"] - eps_b)
        dR = mu / (sl["right"] - eps_b)
        dB = mu / (sl["bottom"] - eps_b)
        dT = mu / (sl["top"] - eps_b)
        dP = mu / (sl["pos"] - eps_b)

        # Left: s = x - r - eps_feas -> ∂x=+1, ∂r=-1
        gx += dL
        gr -= dL
        # Right: s = W - x - r - eps_feas -> ∂x=-1, ∂r=-1
        gx -= dR
        gr -= dR
        # Bottom: s = y - r - eps_feas -> ∂y=+1, ∂r=-1
        gy += dB
        gr -= dB
        # Top: s = H - y - r - eps_feas -> ∂y=-1, ∂r=-1
        gy -= dT
        gr -= dT
        # Positivity: s = r - eps_feas -> ∂r=+1
        gr += dP

        # Pairwise grads
        if sl["pair"].size > 0:
            idx_i, idx_j, dx, dy, dij = sl["pair_ij"]
            denom = mu / (sl["pair"] - eps_b)
            # To avoid zero division (should not happen if feasible), clip dij
            dij_safe = np.maximum(dij, 1e-16)
            # ∂||c_i - c_j|| / ∂x_i = dx/d
            coeff = denom / dij_safe
            # i contributions
            np.add.at(gx, idx_i, coeff * dx)
            np.add.at(gy, idx_i, coeff * dy)
            np.add.at(gr, idx_i, -denom)
            # j contributions
            np.add.at(gx, idx_j, -coeff * dx)
            np.add.at(gy, idx_j, -coeff * dy)
            np.add.at(gr, idx_j, -denom)

        return J, gx, gy, gr

    def all_constraints_feasible(cent: np.ndarray, rad: np.ndarray, pairs_all: Optional[Set[Tuple[int, int]]], eps: float) -> bool:
        x = cent[:, 0]
        y = cent[:, 1]
        if np.any(x - rad < -1e-18):
            return False
        if np.any(W - x - rad < -1e-18):
            return False
        if np.any(y - rad < -1e-18):
            return False
        if np.any(H - y - rad < -1e-18):
            return False
        if np.any(rad < 0.0):
            return False
        if pairs_all is not None:
            for (i, j) in pairs_all:
                dx = cent[i, 0] - cent[j, 0]
                dy = cent[i, 1] - cent[j, 1]
                if np.hypot(dx, dy) < rad[i] + rad[j] - eps - 1e-18:
                    return False
        return True

    improved = False
    sum_before_A2 = sum_r(r)

    # Mini-barrier: μ stages with backtracking and lazy augmentation
    # Ensure initial feasibility with eps_feas
    if all_constraints_feasible(centers_mb, r_mb, adjacency, eps_feas):
        centers_cur = centers_mb.copy()
        r_cur = r_mb.copy()
        for mu, eps_b in zip(mu_schedule, eps_b_schedule):
            # Short ascent per μ
            for _ in range(50):  # 40–60 iterations
                J0, gx, gy, gr = barrier_value_and_grad(centers_cur, r_cur, adjacency, mu, eps_b)
                if not np.isfinite(J0):
                    break  # infeasible for barrier clearance
                step = base_step
                accepted = False
                while step >= min_step:
                    centers_try = centers_cur.copy()
                    centers_try[:, 0] = centers_cur[:, 0] + step * gx
                    centers_try[:, 1] = centers_cur[:, 1] + step * gy
                    r_try = r_cur + step * gr
                    # Check strict barrier feasibility and compute J
                    J1, _, _, _ = barrier_value_and_grad(centers_try, r_try, adjacency, mu, eps_b)
                    if np.isfinite(J1) and J1 > J0 + 1e-18:
                        centers_cur = centers_try
                        r_cur = np.maximum(r_try, 0.0)
                        accepted = True
                        break
                    step *= backtrack
                if not accepted:
                    # Could not improve within backtracking budget; move to next μ
                    pass
            # Lazy augmentation at end of each μ-stage: add violated pairs (s_ij < 0 with eps_feas)
            # Check all pairs
            Nloc = r_cur.size
            for i in range(Nloc):
                for j in range(i + 1, Nloc):
                    dx = centers_cur[i, 0] - centers_cur[j, 0]
                    dy = centers_cur[i, 1] - centers_cur[j, 1]
                    s = np.hypot(dx, dy) - (r_cur[i] + r_cur[j]) - eps_feas
                    if s < 0.0:
                        adjacency.add((i, j))

        # After schedule, acceptance gate for A2: keep only if sum(r) increased vs input to A2
        if sum_r(r_cur) > sum_before_A2 + 1e-15 and all_constraints_feasible(centers_cur, r_cur, adjacency, eps_feas):
            centers, r = centers_cur, r_cur
            improved = True
    # else: infeasible for barrier (should be rare), skip A2

    # A3: LP reallocate (capture created slack), eps_lp_final = 1e-12
    r_lp2, ok2 = _lp_inflate_fixed_centers(centers, W, H, eps=1e-12)
    if ok2 and r_lp2 is not None and sum_r(r_lp2) > sum_r(r) + 1e-15:
        r = r_lp2
        improved = True or improved

    # Return the best (possibly improved) state
    return {"W": W, "H": H, "circles": np.concatenate([centers, r[:, None]], axis=1)}


def construct_packing(num_circles: int = 21) -> np.ndarray:
    """Construct a packing of `num_circles` disjoint circles within a rectangle
    of perimeter exactly 4 using a two-scale rectangular grid plus interstitials,
    followed by optional nonlinear polishing and a final LP–mini-barrier–LP sandwich.

    Algorithm:
      - Enumerate feasible grids (m_x, m_y) and select the best by S.
      - Base geometry r0_exact = 1 / (m_x + m_y), W = 2 m_x r0_exact, H = 2 m_y r0_exact.
      - Place m_x × m_y base circles with radius r0_exact − eps0 and t interstitials with
        radius r1_exact − eps0, chosen by a clearance-aware score. Build a few multi-start
        seeds (for t = 1).
      - LP preselection: for each seed, run a fixed-center LP to maximize sum of radii;
        keep the top-K (K=2) seeds for SLSQP polishing, initializing their radii with
        the LP solution. If SciPy is unavailable, skip this step and keep all seeds.
      - Polish each kept seed with SLSQP under sparse constraints with epsilon annealing
        and post-stage LP reallocation; keep the best polished layout. If SciPy is not
        available, return the best seed by sum of radii.
      - Final endgame LP–mini-barrier–LP sandwich refinement with strict acceptance gates.
      - Final uniform shrink δ = 1e−12 to radii.

    Returns:
      numpy array (num_circles, 3): rows of [x, y, radius].
    """
    if num_circles <= 0:
        return np.zeros((0, 3), dtype=float)

    # 1) Discrete perimeter-balanced search for the grid
    m_x, m_y, t = _select_grid_for_N(num_circles)
    B = m_x * m_y
    assert 0 <= t <= max(0, (m_x - 1) * (m_y - 1))
    assert B + t == num_circles

    # 2) Two-scale seeds with W + H = 2; epsilon annealing seed epsilon
    eps0 = 1e-6
    seeds = _build_initial_circles(m_x, m_y, t, eps0=eps0, multistart_k=6)

    # 2a) LP preselection of seeds (deterministic), keep top K=2 by LP sum of radii,
    # and initialize their radii from the LP solution. If SciPy unavailable/fails, skip.
    K = 2
    try:
        # Probe SciPy availability for linprog
        from scipy.optimize import linprog  # noqa: F401

        # Compute LP radii and sums for all seeds
        seed_scores: List[Tuple[float, int]] = []
        lp_radii_all: Dict[int, np.ndarray] = {}
        for si, seed in enumerate(seeds):
            centers = seed["circles"][:, :2]
            W = seed["W"]
            H = seed["H"]
            r_lp, ok = _lp_inflate_fixed_centers(centers, W, H, eps=eps0)
            if ok and r_lp is not None:
                lp_radii_all[si] = r_lp
                seed_scores.append((float(np.sum(r_lp)), si))
            else:
                seed_scores.append((float(np.sum(seed["circles"][:, 2])), si))

        # Rank and keep top K seeds
        seed_scores.sort(key=lambda t: -t[0])
        keep_indices = [si for (_, si) in seed_scores[: min(K, len(seeds))]]
        new_seeds: List[Dict] = []
        for si in keep_indices:
            seed = seeds[si]
            if si in lp_radii_all:
                seed = dict(seed)  # shallow copy
                circles = seed["circles"].copy()
                circles[:, 2] = lp_radii_all[si]
                seed["circles"] = circles
            new_seeds.append(seed)
        seeds = new_seeds
    except Exception:
        # SciPy not available: skip preselection, keep current behavior
        pass

    # Try polishing with epsilon annealing. If SciPy is unavailable or optimization fails,
    # we will fall back to the best seed by simple sum of radii.
    eps_schedule = [1e-6, 1e-8, 1e-12]
    best_state: Optional[Dict] = None
    best_sum_r = -np.inf

    for seed in seeds:
        polished = _solve_polish(seed, eps_schedule=eps_schedule, max_lazy_iters=3)
        if polished is not None:
            circles = polished["circles"]
            total_r = float(np.sum(circles[:, 2]))
            if total_r > best_sum_r:
                best_sum_r = total_r
                best_state = polished
        else:
            # No SciPy: use the seed directly (with its W, H)
            circles = seed["circles"]
            total_r = float(np.sum(circles[:, 2]))
            if total_r > best_sum_r:
                best_sum_r = total_r
                best_state = {"W": seed["W"], "H": seed["H"], "circles": circles}

    assert best_state is not None

    # 2b) Final LP–mini-barrier–LP sandwich refine with acceptance gates
    best_state = _finalize_refine(best_state, eps_stage=eps_schedule[-1])

    # 3) Final uniform shrink to guarantee strict disjointness
    delta = 1e-12
    circles_out = best_state["circles"].copy()
    circles_out[:, 2] = np.clip(circles_out[:, 2] - delta, 0.0, None)

    return circles_out
# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```

```python
#!/usr/bin/env python3
"""Improved candidate for packing 21 circles in a perimeter-four rectangle.

Implements the discrete perimeter-balanced two-scale construction with polishing:

Core components:
- Discrete perimeter-balanced search for the best m_x × m_y grid and interstitial count t.
- Two-scale seed geometry: base circles arranged on a rectangular grid, plus up to t
  interstitial circles placed at cell centers.
- Clearance-aware interstitial selection with deterministic tie-breaking; multi-start seeds.
- Cheap LP-only preselection over multi-start seeds (if SciPy is available), using
  fixed-center LP with eps_lp_preselect = min(seed_eps, 1e−10), keeping only the top-2
  seeds by Σr_lp for the expensive polishing pipeline and warming their radii from the
  LP solution.
- Optional nonlinear polishing (if SciPy is available) that optimizes centers, radii,
  and rectangle sides W, H under:
    • non-overlap,
    • boundary containment,
    • perimeter budget W + H = 2 (active at optimum).
  Uses sparse adjacency (grid/knn/interstitial neighbors) and lazy constraint augmentation,
  plus epsilon annealing from 1e−6 → 1e−12, with post-stage LP reallocation acceptance gates.
- Final centered uniform inflation + LP acceptance gate (the LP–mini-barrier–LP sandwich),
  then uniform shrink δ = 1e−12 for strict disjointness.

The result is 21 disjoint circles inside an axis-aligned rectangle with perimeter = 4,
with a strong sum of radii for N = 21.
"""

import json
from typing import List, Tuple, Optional, Dict, Set

import numpy as np


# EVOLVE_START
def _select_grid_for_N(N: int) -> Tuple[int, int, int]:
    """Enumerate feasible (m_x, m_y) grids and select the best by the score S.

    A grid (m_x, m_y) can host B = m_x*m_y base circles and up to
    I = (m_x - 1)*(m_y - 1) interstitials. For target N, set t = N - B and
    require 0 <= t <= I.

    Score:
      S = [B + α (N − B)] / (m_x + m_y), where α = sqrt(2) − 1.

    Tie-breakers:
      1) minimize (m_x + m_y)
      2) minimize |m_x − m_y|
      3) lexicographic (m_x, m_y)

    Returns:
      (m_x, m_y, t)
    """
    if N <= 0:
        return (1, 1, 0)

    alpha = np.sqrt(2.0) - 1.0

    best = None
    best_key = None

    # Enumerate reasonable ranges; for safety cap at N (sufficient).
    for mx in range(1, N + 1):
        for my in range(1, N + 1):
            B = mx * my
            if B > N:
                continue
            I = (mx - 1) * (my - 1) if mx > 0 and my > 0 else 0
            t = N - B
            if t < 0 or t > I:
                continue

            S = (B + alpha * (N - B)) / (mx + my)

            # Build sorting key: maximize S -> minimize -S
            key = (-S, mx + my, abs(mx - my), mx, my)
            if best is None or key < best_key:
                best = (mx, my, t)
                best_key = key

    if best is None:
        # Fallback (should not happen with the above enumeration)
        best = (1, N, 0)

    return best


def _grid_seed_geometry(mx: int, my: int) -> Tuple[float, float, float, float]:
    """Return base geometry values for the grid:
    r0_exact, W, H, r1_exact such that W + H = 2 and r1_exact = (sqrt(2) - 1) * r0_exact.
    """
    r0_exact = 1.0 / float(mx + my)
    W = 2.0 * mx * r0_exact
    H = 2.0 * my * r0_exact
    r1_exact = r0_exact * (np.sqrt(2.0) - 1.0)
    return r0_exact, W, H, r1_exact


def _base_centers(mx: int, my: int, W: float, H: float) -> np.ndarray:
    """Compute m_x × m_y base centers on a regular grid."""
    xs = (np.arange(mx) + 0.5) * W / mx
    ys = (np.arange(my) + 0.5) * H / my
    centers = np.array([(x, y) for y in ys for x in xs], dtype=float)
    return centers  # shape (mx*my, 2)


def _cell_centers(mx: int, my: int, W: float, H: float) -> List[Tuple[int, int, float, float]]:
    """Return list of interstitial candidate cell centers with their cell indices (i, j)."""
    out = []
    for j in range(my - 1):
        for i in range(mx - 1):
            x = (i + 1) * W / mx
            y = (j + 1) * H / my
            out.append((i, j, x, y))
    return out


def _clearance_proxy_for_cell(i: int, j: int, mx: int, my: int, W: float, H: float, r0: float) -> float:
    """Compute a local clearance proxy for the interstitial at cell (i, j).

    Proxy is the minimum of:
      - distance to any of the four neighboring base centers minus r0
      - distance to the boundary of the rectangle
    The distances to the four base centers are identical for cell centers in a
    regular grid, but we compute the formula explicitly for clarity.
    """
    # Interstitial center
    x = (i + 1) * W / mx
    y = (j + 1) * H / my
    # Neighbor base centers at the corners of the cell
    cx = [(i + 0.5) * W / mx, (i + 1.5) * W / mx]
    cy = [(j + 0.5) * H / my, (j + 1.5) * H / my]
    dmin_to_base = float("inf")
    for xb in cx:
        for yb in cy:
            d = np.hypot(x - xb, y - yb) - r0
            if d < dmin_to_base:
                dmin_to_base = d
    # Distance to boundary
    d_boundary = min(x, W - x, y, H - y)
    return min(dmin_to_base, d_boundary)


def _interstitial_candidates_ranked(mx: int, my: int, W: float, H: float, r0: float) -> List[Tuple[float, float, int, int, float, float]]:
    """Return ranked interstitial candidates:
    Each item is (score_desc, tie_center_dist2, j, i, x, y).
    We will sort by highest clearance score, then closest to center, then by (j, i).
    """
    cx, cy = W / 2.0, H / 2.0
    ranked: List[Tuple[float, float, int, int, float, float]] = []
    for j in range(my - 1):
        for i in range(mx - 1):
            x = (i + 1) * W / mx
            y = (j + 1) * H / my
            score = _clearance_proxy_for_cell(i, j, mx, my, W, H, r0)
            d2 = (x - cx) ** 2 + (y - cy) ** 2
            ranked.append((score, -d2, j, i, x, y))
    # Sort descending by score (hence reverse), then ascending by distance (since we used -d2),
    # then by j, i for determinism.
    ranked.sort(key=lambda it: (-it[0], it[1], it[2], it[3]))
    return ranked


def _build_initial_circles(mx: int, my: int, t: int, eps0: float, multistart_k: int = 8) -> List[Dict]:
    """Build one or more seed layouts.

    Returns a list of seeds (dicts) with keys:
      - 'W', 'H', 'r0_exact', 'r1_exact', 'circles' (array Nx3), 'types' (list of 'base'/'inter'),
        'meta' (dictionary with indices and mapping details for adjacency),
        'interstitial_cells' (list of chosen cell (i, j))
    If t == 1, we produce up to multistart_k seeds by picking the top-k ranked candidates.
    Otherwise we produce a single seed with the top-t ranked interstitials.
    """
    seeds: List[Dict] = []

    r0_exact, W, H, r1_exact = _grid_seed_geometry(mx, my)
    r0 = max(0.0, r0_exact - eps0)
    r1 = max(0.0, r1_exact - eps0)

    base_centers = _base_centers(mx, my, W, H)
    ranked = _interstitial_candidates_ranked(mx, my, W, H, r0_exact)

    # Utility to build one seed given chosen cells
    def make_seed(chosen_cells: List[Tuple[int, int]]) -> Dict:
        circles: List[Tuple[float, float, float]] = []
        types: List[str] = []
        # Base circles first
        for (x, y) in base_centers:
            circles.append((x, y, r0))
            types.append("base")
        # Interstitials
        for (ci, cj) in chosen_cells:
            x = (ci + 1) * W / mx
            y = (cj + 1) * H / my
            circles.append((x, y, r1))
            types.append("inter")

        circles_arr = np.array(circles, dtype=float)

        # Build meta for adjacency
        # Map base (i,j) -> index
        base_index = {(i, j): (j * mx + i) for j in range(my) for i in range(mx)}
        # Interstitial map: for each chosen cell (ci, cj), record its circle index and its four base neighbors
        inter_map = {}
        for k, (ci, cj) in enumerate(chosen_cells):
            idx = len(base_centers) + k
            neighbors = [(ci, cj), (ci + 1, cj), (ci, cj + 1), (ci + 1, cj + 1)]
            inter_map[(ci, cj)] = {
                "index": idx,
                "base_neighbors": [base_index[p] for p in neighbors],
            }

        meta = {
            "mx": mx,
            "my": my,
            "base_index": base_index,
            "inter_map": inter_map,
            "B": mx * my,
            "t": len(chosen_cells),
        }
        return {
            "W": W,
            "H": H,
            "r0_exact": r0_exact,
            "r1_exact": r1_exact,
            "circles": circles_arr,
            "types": types,
            "meta": meta,
            "interstitial_cells": chosen_cells,
        }

    if t <= 0:
        seeds.append(make_seed([]))
        return seeds

    # If only one interstitial, build multiple seeds (multi-start) from top-k candidates
    if t == 1:
        k = min(multistart_k, len(ranked))
        for h in range(k):
            _, _, j, i, _, _ = ranked[h]
            seeds.append(make_seed([(i, j)]))
    else:
        # Choose top-t candidates deterministically
        chosen: List[Tuple[int, int]] = []
        for h in range(min(t, len(ranked))):
            _, _, j, i, _, _ = ranked[h]
            chosen.append((i, j))
        seeds.append(make_seed(chosen))

    return seeds


def _build_initial_adjacency(seed: Dict) -> Set[Tuple[int, int]]:
    """Build a sparse initial adjacency set for non-overlap constraints.

    Includes:
      - Grid neighbors (4-neighborhood and diagonals) among base circles.
      - Interstitial circle to its four surrounding base circles.
      - k-nearest neighbors (k=6) to capture nearby contacts.
    """
    circles = seed["circles"]
    meta = seed["meta"]
    mx = meta["mx"]
    my = meta["my"]
    B = meta["B"]
    t = meta["t"]

    N = circles.shape[0]
    centers = circles[:, :2]

    pairs: Set[Tuple[int, int]] = set()

    # Base grid neighbors (4-neighborhood + diagonals)
    def idx(i: int, j: int) -> int:
        return j * mx + i

    for j in range(my):
        for i in range(mx):
            a = idx(i, j)
            # Right/left
            if i + 1 < mx:
                b = idx(i + 1, j)
                pairs.add((min(a, b), max(a, b)))
            if j + 1 < my:
                b = idx(i, j + 1)
                pairs.add((min(a, b), max(a, b)))
            # Diagonals
            if i + 1 < mx and j + 1 < my:
                b = idx(i + 1, j + 1)
                pairs.add((min(a, b), max(a, b)))
            if i - 1 >= 0 and j + 1 < my:
                b = idx(i - 1, j + 1)
                pairs.add((min(a, b), max(a, b)))

    # Interstitial neighbors to their four surrounding base circles
    for (ci, cj), imap in meta["inter_map"].items():
        inter_idx = imap["index"]
        for bidx in imap["base_neighbors"]:
            pairs.add((min(inter_idx, bidx), max(inter_idx, bidx)))

    # k-nearest neighbors to capture nearby pairs
    k = 6
    for i in range(N):
        d2 = np.sum((centers - centers[i]) ** 2, axis=1)
        order = np.argsort(d2)
        cnt = 0
        for j in order:
            if j == i:
                continue
            pairs.add((min(i, j), max(i, j)))
            cnt += 1
            if cnt >= k:
                break

    return pairs


def _lp_inflate_fixed_centers(centers: np.ndarray, W: float, H: float, eps: float) -> Tuple[Optional[np.ndarray], bool]:
    """Fixed-center linear program to maximize sum of radii.

    Given fixed centers and rectangle (W, H), solve:
      maximize sum r_i
      subject to:
        0 <= r_i <= min(x_i, W - x_i, y_i, H - y_i) - eps
        r_i + r_j <= ||c_i - c_j|| - eps  for all i < j

    Implemented as minimize -sum r_i, using SciPy linprog (method='highs').
    Returns (radii, True) if successful, else (None, False).
    """
    try:
        from scipy.optimize import linprog
    except Exception:
        return None, False

    N = centers.shape[0]
    # Bounds per variable
    x = centers[:, 0]
    y = centers[:, 1]
    ub = np.minimum.reduce([x, W - x, y, H - y]) - eps
    ub = np.clip(ub, 0.0, None)
    bounds = [(0.0, float(ub[i])) for i in range(N)]

    # Pairwise constraints A_ub r <= b_ub
    # For each pair: (e_i + e_j) r <= d_ij - eps
    pairs_i = []
    pairs_j = []
    b = []
    for i in range(N):
        for j in range(i + 1, N):
            dx = centers[i, 0] - centers[j, 0]
            dy = centers[i, 1] - centers[j, 1]
            dij = float(np.hypot(dx, dy))
            rhs = dij - eps
            # If rhs is negative, the LP will be infeasible; still hand to solver
            pairs_i.append(i)
            pairs_j.append(j)
            b.append(rhs)

    # Build sparse-like A as dense since N is small (<= 21)
    m = len(b)
    if m > 0:
        A = np.zeros((m, N), dtype=float)
        for k, (i, j) in enumerate(zip(pairs_i, pairs_j)):
            A[k, i] = 1.0
            A[k, j] = 1.0
        A_ub = A
        b_ub = np.array(b, dtype=float)
    else:
        A_ub = None
        b_ub = None

    # Objective: minimize -sum r_i
    c = -np.ones(N, dtype=float)

    res = linprog(c=c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
    if not res.success or res.x is None:
        return None, False

    r = np.array(res.x, dtype=float)
    return r, True


def _centered_uniform_inflation(W: float, H: float, centers: np.ndarray, radii: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float, bool]:
    """Compute and apply a centered uniform inflation (about rectangle center).

    Scaling:
      Let (cx, cy) = (W/2, H/2). For t >= 1,
        x' = cx + t (x - cx), y' = cy + t (y - cy), r' = t r.

    Pairwise separations and radii both scale by t, so non-overlap is preserved.
    To maintain boundary containment we require, for each circle:
      x' - r' >= 0
      W - (x' + r') >= 0
      y' - r' >= 0
      H - (y' + r') >= 0

    This yields linear inequalities a + b t >= 0. We find the largest feasible t >= 1.

    Returns (centers', radii', t, applied_flag).
    """
    N = centers.shape[0]
    cx, cy = W / 2.0, H / 2.0
    x = centers[:, 0]
    y = centers[:, 1]
    r = radii

    # Collect bounds on t: lower (from b>0), upper (from b<0)
    t_lower = 1.0
    t_upper = float("inf")

    # For each circle and each boundary inequality
    for i in range(N):
        # Left: cx + t*(x - cx - r) >= 0
        a = cx
        b = (x[i] - cx - r[i])
        if b > 0:
            t_lower = max(t_lower, -a / b)
        elif b < 0:
            t_upper = min(t_upper, -a / b)
        else:
            # b == 0 -> requires a >= 0, which holds since cx >= 0
            pass

        # Right: W - cx - t*(x - cx + r) >= 0
        a = W - cx
        b = -(x[i] - cx + r[i])
        if b > 0:
            t_lower = max(t_lower, -a / b)
        elif b < 0:
            t_upper = min(t_upper, -a / b)

        # Bottom: cy + t*(y - cy - r) >= 0
        a = cy
        b = (y[i] - cy - r[i])
        if b > 0:
            t_lower = max(t_lower, -a / b)
        elif b < 0:
            t_upper = min(t_upper, -a / b)

        # Top: H - cy - t*(y - cy + r) >= 0
        a = H - cy
        b = -(y[i] - cy + r[i])
        if b > 0:
            t_lower = max(t_lower, -a / b)
        elif b < 0:
            t_upper = min(t_upper, -a / b)

    # Numerical guard: lower can't exceed upper; also require t_upper finite
    if not np.isfinite(t_upper):
        t_upper = float("inf")
    # We seek t_max >= 1
    t_max = min(t_upper, max(t_lower, 1.0))

    # Apply only if strictly > 1 by a tiny threshold
    if not (t_max > 1.0 + 1e-12):
        return centers.copy(), radii.copy(), 1.0, False

    t = t_max
    # Apply scaling
    centers_new = np.empty_like(centers)
    centers_new[:, 0] = cx + t * (x - cx)
    centers_new[:, 1] = cy + t * (y - cy)
    radii_new = t * r

    return centers_new, radii_new, t, True


def _solve_polish(seed: Dict, eps_schedule: List[float], max_lazy_iters: int = 3) -> Optional[Dict]:
    """Polish a seed using constrained nonlinear optimization (SLSQP if available).

    Returns a dict with keys:
      - 'W', 'H', 'circles' (Nx3) for the best polished result,
      or None if polishing fails entirely.
    """
    try:
        from scipy.optimize import minimize
    except Exception:
        return None  # SciPy not available

    circles0 = seed["circles"].copy()
    W0 = seed["W"]
    H0 = seed["H"]
    N = circles0.shape[0]

    # Variable layout: v = [W, H, x[0..N-1], y[0..N-1], r[0..N-1]]
    def pack_vars(W, H, centers, radii):
        return np.concatenate(([W, H], centers[:, 0], centers[:, 1], radii))

    def unpack_vars(v):
        W, H = v[0], v[1]
        x = v[2 : 2 + N]
        y = v[2 + N : 2 + 2 * N]
        r = v[2 + 2 * N : 2 + 3 * N]
        centers = np.stack([x, y], axis=1)
        return W, H, centers, r

    # Initial variables
    v0 = pack_vars(W0, H0, circles0[:, :2], circles0[:, 2])

    # Anchor circle: bottom-left among base circles (min y then min x)
    # We enforce touching left and bottom: x_i - r_i = 0 and y_i - r_i = 0.
    # Determine anchor index: among the first B base circles.
    meta = seed["meta"]
    B = meta["B"]
    base_centers = circles0[:B, :2]
    base_order = np.lexsort((base_centers[:, 0], base_centers[:, 1]))  # sort by y, then x
    anchor_idx = int(base_order[0])

    # Build an initial adjacency
    adjacency = _build_initial_adjacency(seed)

    best_result = None
    best_sum_r = -np.inf

    # To support the final centered uniform inflation, keep track of the last accepted (W,H,centers,r)
    last_WHR = None  # tuple (W, H, centers, r)

    # Epsilon annealing loop
    for eps in eps_schedule:
        lazy_iters = 0
        adjacency_current = set(adjacency)

        while lazy_iters <= max_lazy_iters:
            # Build constraints for given eps and adjacency
            cons = []

            # Equality: perimeter W + H = 2
            def ceq_perimeter(v):
                W, H, _, _ = unpack_vars(v)
                return 2.0 - (W + H)

            cons.append({"type": "eq", "fun": ceq_perimeter})

            # Inequalities: W >= 0, H >= 0
            cons.append({"type": "ineq", "fun": lambda v: v[0]})
            cons.append({"type": "ineq", "fun": lambda v: v[1]})

            # Anchor equalities for the bottom-left base circle: x - r = 0 and y - r = 0
            def ceq_anchor_x(v, idx=anchor_idx):
                W, H, centers, r = unpack_vars(v)
                return centers[idx, 0] - r[idx]

            def ceq_anchor_y(v, idx=anchor_idx):
                W, H, centers, r = unpack_vars(v)
                return centers[idx, 1] - r[idx]

            cons.append({"type": "eq", "fun": ceq_anchor_x})
            cons.append({"type": "eq", "fun": ceq_anchor_y})

            # Boundary inequalities for each circle:
            # x_i - r_i >= 0; W - r_i - x_i >= 0; y_i - r_i >= 0; H - r_i - y_i >= 0
            for i in range(N):
                cons.append({"type": "ineq", "fun": (lambda v, i=i: unpack_vars(v)[2][i, 0] - unpack_vars(v)[3][i])})
                cons.append(
                    {"type": "ineq", "fun": (lambda v, i=i: unpack_vars(v)[0] - unpack_vars(v)[3][i] - unpack_vars(v)[2][i, 0])}
                )
                cons.append({"type": "ineq", "fun": (lambda v, i=i: unpack_vars(v)[2][i, 1] - unpack_vars(v)[3][i])})
                cons.append(
                    {"type": "ineq", "fun": (lambda v, i=i: unpack_vars(v)[1] - unpack_vars(v)[3][i] - unpack_vars(v)[2][i, 1])}
                )
                # r_i >= 0
                cons.append({"type": "ineq", "fun": (lambda v, i=i: unpack_vars(v)[3][i])})

            # Non-overlap constraints for pairs in adjacency:
            # Use squared separation: ||c_i - c_j||^2 - (r_i + r_j + eps)^2 >= 0
            for (i, j) in sorted(adjacency_current):
                def g_pair(v, i=i, j=j, eps=eps):
                    _, _, centers, r = unpack_vars(v)
                    dx = centers[i, 0] - centers[j, 0]
                    dy = centers[i, 1] - centers[j, 1]
                    d2 = dx * dx + dy * dy
                    rs = r[i] + r[j] + eps
                    return d2 - rs * rs

                cons.append({"type": "ineq", "fun": g_pair})

            # Objective: minimize negative sum of radii
            def f(v):
                _, _, _, r = unpack_vars(v)
                return -np.sum(r)

            # (Optional) Gradient of objective
            def fprime(v):
                grad = np.zeros_like(v)
                grad[2 + 2 * N :] = -1.0
                return grad

            # Run SLSQP
            res = minimize(
                f,
                v0,
                jac=fprime,
                constraints=cons,
                method="SLSQP",
                options={"maxiter": 400, "ftol": 1e-12, "disp": False},
            )

            # If failure, try to proceed with current x if it exists; otherwise break
            if not res.success:
                v_res = res.x if isinstance(res.x, np.ndarray) else v0
            else:
                v_res = res.x

            # Update starting point for next rounds
            v0 = v_res.copy()

            # Validate and augment constraints lazily: check all pairs
            W, H, centers, r = unpack_vars(v_res)
            violated_pairs: List[Tuple[int, int, float]] = []
            for i in range(N):
                for j in range(i + 1, N):
                    dx = centers[i, 0] - centers[j, 0]
                    dy = centers[i, 1] - centers[j, 1]
                    d2 = dx * dx + dy * dy
                    rs = r[i] + r[j] + eps
                    if d2 < rs * rs - 1e-16:
                        violated_pairs.append((i, j, d2 - rs * rs))

            if violated_pairs:
                # Add these pairs to adjacency and iterate
                for (i, j, _) in violated_pairs:
                    adjacency_current.add((i, j))
                lazy_iters += 1
                continue  # re-solve with augmented adjacency
            else:
                # No violations: accept this eps stage result (with possible LP reallocation)
                sum_r = float(np.sum(r))

                # Post-annealing-stage LP inflation with acceptance gate
                r_lp, ok_lp = _lp_inflate_fixed_centers(centers, W, H, eps=eps)
                if ok_lp:
                    sum_r_lp = float(np.sum(r_lp))
                    if sum_r_lp > sum_r + 1e-15:
                        # Accept LP radii reallocation
                        r = r_lp
                        sum_r = sum_r_lp
                        # Update v0 radii for the next stage start
                        v0 = pack_vars(W, H, centers, r)

                # Update best trackers and last stage state
                if sum_r > best_sum_r:
                    best_sum_r = sum_r
                    best_result = {"W": W, "H": H, "circles": np.concatenate([centers, r[:, None]], axis=1)}
                last_WHR = (W, H, centers.copy(), r.copy())

                break  # move to next epsilon

        # proceed to next epsilon stage (or exit if max lazy iters exceeded)

    # After the final epsilon stage converges, attempt a centered uniform inflation + LP,
    # accepting only if it improves the sum of radii.
    if last_WHR is not None:
        W, H, centers, r = last_WHR
        centers_scaled, r_scaled, t, applied = _centered_uniform_inflation(W, H, centers, r)
        if applied:
            # Run LP after scaling; use the tightest eps (final stage), i.e., last element of schedule
            eps_final = eps_schedule[-1] if len(eps_schedule) > 0 else 1e-12
            r_lp, ok_lp = _lp_inflate_fixed_centers(centers_scaled, W, H, eps=eps_final)
            if ok_lp:
                sum_old = float(np.sum(r))
                sum_new = float(np.sum(r_lp))
                if sum_new > sum_old + 1e-15:
                    # Accept improved configuration
                    best_sum_r = sum_new
                    best_result = {
                        "W": W,
                        "H": H,
                        "circles": np.concatenate([centers_scaled, r_lp[:, None]], axis=1),
                    }

    return best_result


def construct_packing(num_circles: int = 21) -> np.ndarray:
    """Construct a packing of `num_circles` disjoint circles within a rectangle
    of perimeter exactly 4 using a two-scale rectangular grid plus interstitials,
    followed by optional nonlinear polishing.

    Algorithm:
      - Enumerate feasible grids (m_x, m_y) and select the best by S.
      - Base geometry r0_exact = 1 / (m_x + m_y), W = 2 m_x r0_exact, H = 2 m_y r0_exact.
      - Place m_x × m_y base circles with radius r0_exact − eps0 and t interstitials with
        radius r1_exact − eps0, chosen by a clearance-aware score. Build a few multi-start
        seeds (for t = 1).
      - LP preselection: for each seed, run a fixed-center LP to maximize sum of radii with
        eps_lp_preselect = min(eps0, 1e−10); keep the top-K (K=min(2, number_of_seeds)) seeds
        for SLSQP polishing, initializing their radii with the LP solution. If SciPy is unavailable,
        skip this step and keep current behavior.
      - Polish each kept seed with SLSQP under sparse constraints with epsilon annealing
        and post-stage LP reallocation; keep the best polished layout. If SciPy is not
        available, return the best seed by sum of radii.
      - Final centered uniform inflation + LP acceptance gate, then finalize with a
        uniform shrink δ = 1e−12 to radii.

    Returns:
      numpy array (num_circles, 3): rows of [x, y, radius].
    """
    if num_circles <= 0:
        return np.zeros((0, 3), dtype=float)

    # 1) Discrete perimeter-balanced search for the grid
    m_x, m_y, t = _select_grid_for_N(num_circles)
    B = m_x * m_y
    assert 0 <= t <= max(0, (m_x - 1) * (m_y - 1))
    assert B + t == num_circles

    # 2) Two-scale seeds with W + H = 2; epsilon annealing seed epsilon
    eps0 = 1e-6
    # Increase multistart_k modestly to 8 for broader exploration under cheap LP preselection
    seeds = _build_initial_circles(m_x, m_y, t, eps0=eps0, multistart_k=8)

    # 2a) LP preselection of seeds (deterministic), keep top K=min(2, #seeds) by LP sum of radii,
    # and initialize their radii from the LP solution. If SciPy unavailable/fails, skip.
    K = 2
    eps_lp_preselect = min(eps0, 1e-10)
    try:
        # Probe SciPy availability for linprog
        from scipy.optimize import linprog  # noqa: F401

        # Compute LP radii and sums for all seeds
        seed_scores: List[Tuple[float, int]] = []
        lp_radii_all: Dict[int, np.ndarray] = {}
        for si, seed in enumerate(seeds):
            centers = seed["circles"][:, :2]
            W = seed["W"]
            H = seed["H"]
            r_lp, ok = _lp_inflate_fixed_centers(centers, W, H, eps=eps_lp_preselect)
            if ok and r_lp is not None:
                lp_radii_all[si] = r_lp
                seed_scores.append((float(np.sum(r_lp)), si))
            else:
                # Fall back to current radii for scoring if LP failed on this seed
                seed_scores.append((float(np.sum(seed["circles"][:, 2])), si))

        # Rank and keep top K seeds
        seed_scores.sort(key=lambda t: -t[0])
        keep_indices = [si for (_, si) in seed_scores[: min(K, len(seeds))]]
        new_seeds: List[Dict] = []
        for si in keep_indices:
            seed = seeds[si]
            # Initialize radii with LP radii if present; otherwise leave unchanged
            if si in lp_radii_all:
                seed = dict(seed)  # shallow copy
                circles = seed["circles"].copy()
                circles[:, 2] = lp_radii_all[si]
                seed["circles"] = circles
            new_seeds.append(seed)
        seeds = new_seeds
    except Exception:
        # SciPy not available: skip preselection, keep current behavior
        pass

    # Try polishing with epsilon annealing. If SciPy is unavailable or optimization fails,
    # we will fall back to the best seed by simple sum of radii.
    eps_schedule = [1e-6, 1e-8, 1e-12]
    best_layout = None
    best_sum_r = -np.inf

    for seed in seeds:
        polished = _solve_polish(seed, eps_schedule=eps_schedule, max_lazy_iters=3)
        if polished is not None:
            circles = polished["circles"]
            total_r = float(np.sum(circles[:, 2]))
            if total_r > best_sum_r:
                best_sum_r = total_r
                best_layout = circles
        else:
            # No SciPy or failure: use the seed directly
            circles = seed["circles"]
            total_r = float(np.sum(circles[:, 2]))
            if total_r > best_sum_r:
                best_sum_r = total_r
                best_layout = circles

    # 3) Final uniform shrink to guarantee strict disjointness
    delta = 1e-12
    circles_out = best_layout.copy()
    circles_out[:, 2] = np.clip(circles_out[:, 2] - delta, 0.0, None)

    return circles_out
# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```

```python
#!/usr/bin/env python3
"""Improved candidate for packing 21 circles inside the unit square to maximize sum of radii.

Algorithm overview (implements the provided high-level description):
- Multi-start hexagonal-lattice seeding with jitter and multiple row-count patterns that sum to 21.
- LP preselection using a fixed-center LP (SciPy HiGHS if available, greedy fallback otherwise).
- Interior log-barrier ascent on (x, y, r) with a decreasing barrier weight schedule.
- Periodic fixed-center LP to reallocate radii exactly (accept only if sum(r) increases).
- Centered uniform inflation (monotone increase) followed by LP.
- Endgame LP–mini-barrier–LP sandwich with epsilon annealing to tighten near-tangencies.
- Strict feasibility polish: clip to box and eliminate residual overlaps by tiny shrinkage.

The packing is constrained to the unit square, which ensures the minimal circumscribing
axis-aligned rectangle has perimeter at most 4. We aim to maximize sum of radii.
"""

import json
from typing import List, Tuple, Optional

import numpy as np

# Try to import a linear programming solver (SciPy's HiGHS). If unavailable, we'll gracefully fall back.
try:
    from scipy.optimize import linprog  # type: ignore

    HAS_SCIPY = True
except Exception:
    HAS_SCIPY = False


# =========================
# Utility and core routines
# =========================

def compute_boundary_limits(xy: np.ndarray, eps: float) -> np.ndarray:
    """Compute per-circle maximum radius allowed by box boundaries with margin eps."""
    x = xy[:, 0]
    y = xy[:, 1]
    m = np.minimum(np.minimum(x, 1.0 - x), np.minimum(y, 1.0 - y)) - eps
    return np.maximum(m, 0.0)


def pairwise_dists(xy: np.ndarray) -> np.ndarray:
    """Compute Euclidean pairwise distances for centers; N x N symmetric with zeros on diagonal."""
    diff = xy[:, None, :] - xy[None, :, :]
    D = np.sqrt(np.maximum(np.sum(diff * diff, axis=2), 0.0))
    return D


def fixed_center_lp_radii(
    xy: np.ndarray, eps: float = 1e-10, prefer_scipy: bool = True
) -> Tuple[np.ndarray, float, bool]:
    """Solve the fixed-center LP: maximize sum(r) subject to
       - 0 <= r_i <= m_i (boundary)
       - r_i + r_j <= d_ij - eps for all i<j

    Returns (r, sum_r, ok). If no LP solver available, use a greedy feasible projection fallback.
    """
    N = xy.shape[0]
    m = compute_boundary_limits(xy, eps)

    # Quick infeasibility guard: if any m < 0, clamp to 0
    m = np.maximum(m, 0.0)

    # If SciPy is available, use HiGHS which is fast and robust for small LPs
    if HAS_SCIPY and prefer_scipy:
        D = pairwise_dists(xy)
        # Bounds: 0 <= r_i <= m_i
        bounds = [(0.0, float(m_i)) for m_i in m]
        # Objective: maximize sum r_i => minimize -sum r_i
        c = -np.ones(N, dtype=float)

        # Constraints: A_ub @ r <= b_ub
        # For every pair i<j: e_i + e_j <= D[i,j] - eps
        # We'll build rows on the fly.
        rows = []
        rhs = []
        for i in range(N):
            for j in range(i + 1, N):
                rhs_ij = D[i, j] - eps
                # If rhs_ij <= 0, then the only feasible solution is with very small radii near zero for i or j.
                # Keep the constraint anyway; the LP will set radii accordingly.
                row = np.zeros(N, dtype=float)
                row[i] = 1.0
                row[j] = 1.0
                rows.append(row)
                rhs.append(rhs_ij)

        if rows:
            A_ub = np.vstack(rows)
            b_ub = np.array(rhs, dtype=float)
        else:
            A_ub = None
            b_ub = None

        try:
            res = linprog(
                c=c,
                A_ub=A_ub,
                b_ub=b_ub,
                bounds=bounds,
                method="highs",
            )
            if res.success and res.x is not None:
                r = np.clip(res.x, 0.0, m)
                sum_r = float(np.sum(r))
                return r, sum_r, True
        except Exception:
            # fall back if solver errors
            pass

    # Greedy fallback: start from boundary limits, then reduce to satisfy pairwise constraints iteratively.
    # Not optimal but feasible and fast.
    r = m.copy()
    D = pairwise_dists(xy)
    # Iteratively enforce r_i + r_j <= D_ij - eps by shrinking the larger of the two
    for _ in range(4 * N * N):
        changed = False
        for i in range(N):
            for j in range(i + 1, N):
                rhs_ij = D[i, j] - eps
                if rhs_ij < 0:
                    rhs_ij = 0.0
                total = r[i] + r[j]
                if total > rhs_ij + 1e-15:
                    # shrink the larger one just enough
                    excess = total - rhs_ij
                    if r[i] >= r[j]:
                        r[i] = max(r[i] - excess, 0.0)
                    else:
                        r[j] = max(r[j] - excess, 0.0)
                    changed = True
        if not changed:
            break
    r = np.clip(r, 0.0, m)
    return r, float(np.sum(r)), True


def strictly_feasible_init_radii(xy: np.ndarray, eps: float) -> np.ndarray:
    """Construct a strictly feasible small initial radii vector for given centers."""
    m = compute_boundary_limits(xy, eps)
    # Small fraction of boundary limit to guarantee feasibility for pairwise constraints
    r = np.minimum(m, 0.01)
    # Slight uniform shrink to ensure strict positivity in logs
    r *= 0.95
    return r


def barrier_objective_and_grad(xy: np.ndarray, r: np.ndarray, mu: float, eps: float) -> Tuple[float, np.ndarray, np.ndarray, np.ndarray, bool]:
    """Compute f = sum(r) + mu * sum(log(slack)) and its gradient wrt x, y, r.

    Returns tuple (f, gx, gy, gr, ok) where ok indicates all slacks are positive.
    """
    N = xy.shape[0]
    x = xy[:, 0].copy()
    y = xy[:, 1].copy()
    # Initialize gradients
    gx = np.zeros(N, dtype=float)
    gy = np.zeros(N, dtype=float)
    gr = np.ones(N, dtype=float)  # d/d r of sum(r) contributes 1

    # Start with objective from sum(r)
    f = float(np.sum(r))

    # Boundary slacks
    s_left = x - r - eps
    s_right = 1.0 - x - r - eps
    s_bottom = y - r - eps
    s_top = 1.0 - y - r - eps

    if (
        np.min(s_left) <= 0.0
        or np.min(s_right) <= 0.0
        or np.min(s_bottom) <= 0.0
        or np.min(s_top) <= 0.0
    ):
        return -np.inf, gx, gy, gr, False

    # Add boundary logs
    f += mu * (
        np.sum(np.log(s_left))
        + np.sum(np.log(s_right))
        + np.sum(np.log(s_bottom))
        + np.sum(np.log(s_top))
    )

    inv_s_left = mu / s_left
    inv_s_right = mu / s_right
    inv_s_bottom = mu / s_bottom
    inv_s_top = mu / s_top

    # Gradients from boundary constraints
    gx += inv_s_left * 1.0
    gx += inv_s_right * (-1.0)
    gy += inv_s_bottom * 1.0
    gy += inv_s_top * (-1.0)

    gr += (-inv_s_left) + (-inv_s_right) + (-inv_s_bottom) + (-inv_s_top)

    # Pairwise slacks
    D = pairwise_dists(xy)
    ok = True
    for i in range(N):
        xi = x[i]
        yi = y[i]
        for j in range(i + 1, N):
            dx = xi - x[j]
            dy = yi - y[j]
            dij = D[i, j]
            if dij <= 0:
                dij = 1e-16  # avoid division by zero
            s_ij = dij - r[i] - r[j] - eps
            if s_ij <= 0.0:
                ok = False
                # No need to continue; but accumulate nothing further
                continue
            # Contribution to objective
            f += mu * np.log(s_ij)
            inv = mu / (s_ij * dij)
            # gradients on centers
            gx[i] += inv * dx
            gy[i] += inv * dy
            gx[j] -= inv * dx
            gy[j] -= inv * dy
            # gradients on radii
            inv_r = mu / s_ij
            gr[i] += -inv_r
            gr[j] += -inv_r

    if not ok:
        return -np.inf, gx, gy, gr, False

    return f, gx, gy, gr, True


def enforce_strict_feasibility(xy: np.ndarray, r: np.ndarray, eps: float) -> Tuple[np.ndarray, np.ndarray]:
    """Clip centers to [r+eps, 1-r-eps] and ensure radii minimally positive."""
    x = xy[:, 0]
    y = xy[:, 1]
    # Clip centers
    x = np.clip(x, r + eps, 1.0 - r - eps)
    y = np.clip(y, r + eps, 1.0 - r - eps)
    # Ensure radii are >= tiny
    r = np.maximum(r, 1e-9)
    return np.column_stack((x, y)), r


def overlap_polish(xy: np.ndarray, r: np.ndarray, eps: float, max_iters: int = 200) -> np.ndarray:
    """Resolve any lingering pairwise overlaps by shrinking radii slightly.
    This preserves boundary feasibility and yields strictly positive slacks."""
    N = len(r)
    for _ in range(max_iters):
        D = pairwise_dists(xy)
        worst = 0.0
        worst_pair = None
        for i in range(N):
            for j in range(i + 1, N):
                s_ij = D[i, j] - r[i] - r[j] - eps
                if s_ij < worst:
                    worst = s_ij
                    worst_pair = (i, j)
        if worst_pair is None or worst >= 0.0:
            break
        i, j = worst_pair
        deficit = -worst + 1e-12
        # shrink the larger radius by deficit
        if r[i] >= r[j]:
            r[i] = max(r[i] - deficit, 1e-9)
        else:
            r[j] = max(r[j] - deficit, 1e-9)
    return r


def backtracking_barrier_ascent(
    xy: np.ndarray,
    r: np.ndarray,
    mu: float,
    eps: float,
    max_iters: int = 120,
    init_step: float = 0.05,
    shrink: float = 0.5,
) -> Tuple[np.ndarray, np.ndarray]:
    """Run a simple gradient ascent on the barrier-augmented objective, maintaining strict feasibility."""
    xy = xy.copy()
    r = r.copy()
    N = xy.shape[0]

    # Tiny pre-shrink to ensure strict positivity for slacks at start of stage
    r *= 0.999999

    # Compute initial objective and gradient
    f, gx, gy, gr, ok = barrier_objective_and_grad(xy, r, mu, eps)
    if not ok:
        # If somehow infeasible, slightly shrink radii uniformly until feasible
        for _ in range(20):
            r *= 0.9
            f, gx, gy, gr, ok = barrier_objective_and_grad(xy, r, mu, eps)
            if ok:
                break
        if not ok:
            return xy, r

    step = init_step
    for _ in range(max_iters):
        # Normalize gradient vector to unit norm to set the direction
        g_norm = np.sqrt(np.sum(gx * gx) + np.sum(gy * gy) + np.sum(gr * gr))
        if not np.isfinite(g_norm) or g_norm < 1e-12:
            break
        dx = (gx / (g_norm + 1e-18)) * step
        dy = (gy / (g_norm + 1e-18)) * step
        dr = (gr / (g_norm + 1e-18)) * step

        # Try backtracking to ensure feasibility and objective increase
        improved = False
        current_xy = xy
        current_r = r
        for _bt in range(20):
            trial_xy = np.empty_like(xy)
            trial_xy[:, 0] = current_xy[:, 0] + dx
            trial_xy[:, 1] = current_xy[:, 1] + dy
            trial_r = current_r + dr

            # Enforce minimal radii positivity and keep centers within [r+eps, 1-r-eps] softly
            trial_r = np.maximum(trial_r, 1e-12)
            # Soft clip centers — if clipping is needed, we still consider it a valid step
            trial_xy[:, 0] = np.clip(trial_xy[:, 0], trial_r + eps, 1.0 - trial_r - eps)
            trial_xy[:, 1] = np.clip(trial_xy[:, 1], trial_r + eps, 1.0 - trial_r - eps)

            f_new, gx_new, gy_new, gr_new, ok_new = barrier_objective_and_grad(trial_xy, trial_r, mu, eps)
            if ok_new and np.isfinite(f_new) and f_new > f + 1e-12:
                # Accept step
                xy = trial_xy
                r = trial_r
                f = f_new
                gx, gy, gr = gx_new, gy_new, gr_new
                improved = True
                break
            else:
                # shrink step
                dx *= shrink
                dy *= shrink
                dr *= shrink
        if not improved:
            # Could not improve further
            break

    return xy, r


def centered_uniform_inflation(xy: np.ndarray, r: np.ndarray, eps: float) -> Tuple[np.ndarray, np.ndarray, float]:
    """Uniformly scale centers and radii about (0.5, 0.5) by the maximal t >= 1
    that preserves boundary feasibility. Pairwise feasibility is preserved and improves under uniform expansion.
    Returns (xy_new, r_new, t)."""
    x = xy[:, 0]
    y = xy[:, 1]
    N = len(r)

    # Boundary slacks at t=1 and their slopes in t
    # For each circle, four constraints of the form s(t) = s0 + b*(t-1) >= 0
    # Left: s0 = x - r - eps; b = (x - 0.5) - r
    # Right: s0 = 1 - x - r - eps; b = -((x - 0.5) + r)
    # Bottom: s0 = y - r - eps; b = (y - 0.5) - r
    # Top: s0 = 1 - y - r - eps; b = -((y - 0.5) + r)
    s0_left = x - r - eps
    b_left = (x - 0.5) - r
    s0_right = 1.0 - x - r - eps
    b_right = -((x - 0.5) + r)
    s0_bottom = y - r - eps
    b_bottom = (y - 0.5) - r
    s0_top = 1.0 - y - r - eps
    b_top = -((y - 0.5) + r)

    t_candidates = [np.inf]

    def add_bounds(s0: np.ndarray, b: np.ndarray):
        for si, bi in zip(s0, b):
            if bi < 0.0:
                # t <= 1 - si/bi
                t_lim = 1.0 - si / bi
                if t_lim > 1.0:
                    t_candidates.append(t_lim)
                else:
                    # Already limiting at or below 1, include it anyway
                    t_candidates.append(t_lim)

    add_bounds(s0_left, b_left)
    add_bounds(s0_right, b_right)
    add_bounds(s0_bottom, b_bottom)
    add_bounds(s0_top, b_top)

    t_max = min(t_candidates)
    if not np.isfinite(t_max):
        t_max = 1.0
    t = max(1.0, t_max)

    # Apply scaling
    if t <= 1.0 + 1e-12:
        return xy, r, 1.0

    xc = 0.5
    yc = 0.5
    xy_new = np.empty_like(xy)
    xy_new[:, 0] = xc + t * (x - xc)
    xy_new[:, 1] = yc + t * (y - yc)
    r_new = t * r

    # Final clip to maintain strict feasibility (should be fine given construction)
    xy_new, r_new = enforce_strict_feasibility(xy_new, r_new, eps)
    return xy_new, r_new, t


def generate_hex_seeds(num_circles: int = 21, rng: Optional[np.random.Generator] = None) -> List[np.ndarray]:
    """Generate multiple hexagonal-staggered seeds that sum to the given number of circles."""
    if rng is None:
        rng = np.random.default_rng(12345)

    patterns = [
        [5, 4, 4, 4, 4],
        [4, 5, 4, 4, 4],
        [4, 4, 5, 4, 4],
        [4, 4, 4, 5, 4],
        [5, 4, 5, 4, 3],
    ]
    # Ensure patterns sum to num_circles (21)
    patterns = [p for p in patterns if sum(p) == num_circles]

    spacings = [0.2 * 0.98, 0.2, 0.2 * 1.02]
    seeds: List[np.ndarray] = []
    margin = 1e-3

    for counts in patterns:
        R = len(counts)  # number of rows
        for s in spacings:
            hy = s * np.sqrt(3) / 2.0
            ys = np.array([0.5 + (i - (R - 1) / 2.0) * hy for i in range(R)], dtype=float)
            # Prepare centers
            centers = []
            for i_row, c in enumerate(counts):
                # True hex staggering: alternate rows offset by half step in x
                offset = 0.5 * s if (i_row % 2 == 1) else 0.0
                xs = np.array([0.5 + (j - (c - 1) / 2.0) * s + offset for j in range(c)], dtype=float)
                for x in xs:
                    centers.append([x, ys[i_row]])
            centers = np.array(centers, dtype=float)
            # Jitter to break symmetry and avoid exact equal distances
            centers += rng.uniform(low=-1e-3, high=1e-3, size=centers.shape)
            # Clip to unit square with a tiny margin
            centers[:, 0] = np.clip(centers[:, 0], margin, 1.0 - margin)
            centers[:, 1] = np.clip(centers[:, 1], margin, 1.0 - margin)
            seeds.append(centers)

    # If no patterns matched, fall back to a 5x5 grid picking the first 21
    if not seeds:
        grid = np.array([[((c + 0.5) / 5.0), ((r + 0.5) / 5.0)] for r in range(5) for c in range(5)], dtype=float)
        seeds.append(grid[:num_circles])

    return seeds


def optimize_seed(
    centers: np.ndarray,
    rng: Optional[np.random.Generator] = None,
    K_preselect: int = 2,
) -> Tuple[np.ndarray, np.ndarray, float]:
    """Run the full pipeline for one seed: LP preselection across all seeds happens outside.
    This function runs the barrier ascent, LP interleaves, uniform inflation, sandwich, and feasibility polish.
    Returns (centers, radii, sum_r)."""

    if rng is None:
        rng = np.random.default_rng(2024)

    N = centers.shape[0]

    # Epsilon schedule
    eps_init = 1e-6
    eps_lp_mid = 1e-9
    eps_final = 1e-12

    # Initialize radii strictly feasible
    r = strictly_feasible_init_radii(centers, eps_init)
    xy = centers.copy()

    # Initial LP to allocate radii (accept only if it increases sum)
    r_lp, sum_lp, ok_lp = fixed_center_lp_radii(xy, eps=eps_init)
    if ok_lp and sum_lp > float(np.sum(r)) + 1e-14:
        r = r_lp.copy()

    # Barrier ascent schedule
    mu_schedule = [5e-2, 2e-2, 1e-2, 5e-3, 3e-3, 1e-3]
    for mu in mu_schedule:
        xy, r = backtracking_barrier_ascent(xy, r, mu=mu, eps=eps_init, max_iters=120, init_step=0.05)
        # Re-allocate radii at fixed centers
        r_lp, sum_lp, ok_lp = fixed_center_lp_radii(xy, eps=eps_init)
        if ok_lp and sum_lp > float(np.sum(r)) + 1e-14:
            r = r_lp.copy()

    # Centered uniform inflation followed by LP
    xy2, r2, t = centered_uniform_inflation(xy, r, eps=eps_init)
    if t > 1.0 + 1e-12:
        # LP with same epsilon
        r_lp, sum_lp, ok_lp = fixed_center_lp_radii(xy2, eps=eps_init)
        sum_r2 = float(np.sum(r2))
        if ok_lp and sum_lp > sum_r2 + 1e-14:
            # Accept LP radii at inflated centers
            xy = xy2
            r = r_lp
        elif sum_r2 > float(np.sum(r)) + 1e-14:
            # Accept uniform inflation alone
            xy = xy2
            r = r2

    # Anneal epsilon down and run LP–mini-barrier–LP sandwich
    # First a tighter LP
    r_lp, sum_lp, ok_lp = fixed_center_lp_radii(xy, eps=eps_lp_mid)
    if ok_lp and sum_lp > float(np.sum(r)) + 1e-14:
        r = r_lp

    # Tiny pre-shrink to ensure strict feasibility for smaller eps barriers
    r *= 0.999999
    # Short mini-barrier at smaller eps with small mus
    mini_mus = [2e-3, 1e-3, 5e-4]
    for mu in mini_mus:
        xy, r = backtracking_barrier_ascent(xy, r, mu=mu, eps=eps_lp_mid, max_iters=80, init_step=0.03)

    # LP again with tighter eps
    r_lp, sum_lp, ok_lp = fixed_center_lp_radii(xy, eps=eps_lp_mid)
    if ok_lp and sum_lp > float(np.sum(r)) + 1e-14:
        r = r_lp

    # Optional: another centered uniform inflation + LP with tighter eps
    xy2, r2, t = centered_uniform_inflation(xy, r, eps=eps_lp_mid)
    if t > 1.0 + 1e-12:
        # LP at fixed centers
        r_lp, sum_lp, ok_lp = fixed_center_lp_radii(xy2, eps=eps_lp_mid)
        sum_r2 = float(np.sum(r2))
        if ok_lp and sum_lp > sum_r2 + 1e-14:
            xy = xy2
            r = r_lp
        elif sum_r2 > float(np.sum(r)) + 1e-14:
            xy = xy2
            r = r2

    # Final LP at very small epsilon to reclaim tiny slack
    r_lp, sum_lp, ok_lp = fixed_center_lp_radii(xy, eps=eps_final)
    if ok_lp and sum_lp > float(np.sum(r)) + 1e-14:
        r = r_lp

    # Strict feasibility polish
    xy, r = enforce_strict_feasibility(xy, r, eps_final)
    r = overlap_polish(xy, r, eps=eps_final, max_iters=200)

    return xy, r, float(np.sum(r))


def multi_start_optimize(num_circles: int = 21) -> Tuple[np.ndarray, np.ndarray]:
    """Generate seeds, preselect via LP, then run full optimization for the top seeds.
    Returns the best packing found (centers, radii)."""

    rng = np.random.default_rng(2025)
    seeds = generate_hex_seeds(num_circles=num_circles, rng=rng)

    # LP preselection: solve the fixed-center LP for each seed and score by sum(r)
    scores: List[Tuple[float, int, np.ndarray, np.ndarray]] = []
    for idx, seed in enumerate(seeds):
        r_lp, sum_lp, ok = fixed_center_lp_radii(seed, eps=1e-10)
        if ok:
            scores.append((sum_lp, idx, seed, r_lp))
        else:
            # Fallback scoring by boundary-only sum
            m = compute_boundary_limits(seed, eps=1e-10)
            scores.append((float(np.sum(m)), idx, seed, m))

    # Sort by score descending and keep top-K seeds for polishing
    K = min(2, len(scores))
    scores.sort(key=lambda t: t[0], reverse=True)
    top = scores[:K] if K > 0 else list()

    best_sum = -1.0
    best_xy = None
    best_r = None

    if not top:
        # No LP available, run all seeds
        top = [(0.0, i, s, strictly_feasible_init_radii(s, 1e-6)) for i, s in enumerate(seeds)]

    for _, _, seed_xy, _ in top:
        xy, r, sum_r = optimize_seed(seed_xy, rng=rng)
        if sum_r > best_sum + 1e-14:
            best_sum = sum_r
            best_xy = xy
            best_r = r

    assert best_xy is not None and best_r is not None
    return best_xy, best_r


# EVOLVE_START
def construct_packing(num_circles: int = 21):
    """Build the packing using the enhanced child algorithm implementing the two targeted changes:
    1) Broadened hex-lattice spacing and slightly larger LP-only survivor pool with warm starts:
       - spacings s ∈ {0.2×0.96, 0.2×0.98, 0.2, 0.2×1.02, 0.2×1.04}
       - jittered restarts per (pattern, spacing, aspect) raised to 8
       - LP-only preselection survivors per (pattern, spacing) raised to top_K = 3
       - LP preselection uses eps_lp = 1e−10 and forwards the LP radii as warm starts
    2) Adaptive LP correction cadence inside growth-and-move:
       - periodic fixed-center LP correction fires adaptively with K in [20, 60] adjusted each trust block B=50
       - K shrinks when blocks stall or near-active pairs increase; relaxes otherwise
       - LP updates are only committed if sum(r) strictly increases (acceptance gating)
    Other stages (uniform inflations, LP–mini-barrier–LP sandwich, and final polish) are preserved.
    """
    assert num_circles == 21, "This solver is tuned for exactly 21 circles."

    rng = np.random.default_rng(30303)

    # Patterns (row counts summing to 21). We preserve parent's diversified patterns set.
    patterns = [
        [5, 4, 4, 4, 4],
        [4, 5, 4, 4, 4],
        [4, 4, 5, 4, 4],
        [4, 4, 4, 5, 4],
        [5, 4, 5, 4, 3],
        [5, 5, 4, 4, 3],
        [4, 5, 5, 4, 3],
        [5, 5, 5, 3, 3],
        [6, 4, 4, 4, 3],
    ]
    patterns = [p for p in patterns if sum(p) == num_circles]

    # Change 1a: broaden spacings to five scales
    spacings = [0.2 * 0.96, 0.2 * 0.98, 0.2, 0.2 * 1.02, 0.2 * 1.04]
    # Preserve aspect-ratio exploration from parent; we will group by (pattern, spacing) in selection.
    aspect_W = [0.97, 1.00, 1.03]  # H = 2 - W
    margin = 1e-3

    def make_seed_in_rect(counts: List[int], s: float, W: float, H: float) -> np.ndarray:
        """Construct hex-staggered centers in rectangle [0,W]x[0,H], then map back to unit square."""
        R = len(counts)
        hy = s * np.sqrt(3) / 2.0
        ys = np.array([0.5 * H + (i - (R - 1) / 2.0) * hy for i in range(R)], dtype=float)
        centers = []
        for i_row, c in enumerate(counts):
            offset = 0.5 * s if (i_row % 2 == 1) else 0.0
            xs = np.array([0.5 * W + (j - (c - 1) / 2.0) * s + offset for j in range(c)], dtype=float)
            for x in xs:
                centers.append([x, ys[i_row]])
        centers = np.array(centers, dtype=float)
        # Clip inside the rectangle to avoid edges
        centers[:, 0] = np.clip(centers[:, 0], margin, W - margin)
        centers[:, 1] = np.clip(centers[:, 1], margin, H - margin)
        # Map affinely back to unit square
        centers[:, 0] /= W
        centers[:, 1] /= H
        # Tiny jitter to break symmetry
        centers += rng.uniform(low=-1e-3, high=1e-3, size=centers.shape)
        centers[:, 0] = np.clip(centers[:, 0], margin, 1.0 - margin)
        centers[:, 1] = np.clip(centers[:, 1], margin, 1.0 - margin)
        return centers

    # Generate seed pool with metadata for grouping by (pattern, spacing)
    SeedItem = Tuple[Tuple[int, ...], float, np.ndarray]  # (pattern tuple, spacing, centers)
    all_seed_items: List[SeedItem] = []
    jitter_count = 8  # Change 1b: increase jittered restarts per seed to 8
    for counts in patterns:
        counts_t = tuple(counts)
        for s in spacings:
            for W in aspect_W:
                H = 2.0 - W
                base = make_seed_in_rect(counts, s, W, H)
                all_seed_items.append((counts_t, s, base))
                for _ in range(jitter_count):
                    jitter = base + rng.uniform(low=-8e-4, high=8e-4, size=base.shape)
                    jitter[:, 0] = np.clip(jitter[:, 0], margin, 1.0 - margin)
                    jitter[:, 1] = np.clip(jitter[:, 1], margin, 1.0 - margin)
                    all_seed_items.append((counts_t, s, jitter))

    if not all_seed_items:
        # Fallback: uniform grid selecting first 21 points
        grid = np.array([[((c + 0.5) / 5.0), ((r + 0.5) / 5.0)] for r in range(5) for c in range(5)], dtype=float)
        all_seed_items.append((tuple([5, 4, 4, 4, 4]), 0.2, grid[:num_circles]))

    # Change 1c: LP-only preselection with eps_lp = 1e-10 and survivors top_K=3 per (pattern, spacing).
    eps_lp_pre = 1e-10
    from collections import defaultdict

    # Map group -> list of (score, seed_xy, r_lp)
    groups: defaultdict = defaultdict(list)
    for counts_t, s, seed_xy in all_seed_items:
        r_lp, sum_lp, ok = fixed_center_lp_radii(seed_xy, eps=eps_lp_pre)
        if not ok:
            # Fallback scoring by boundary-only sum
            r_lp = compute_boundary_limits(seed_xy, eps=eps_lp_pre)
            sum_lp = float(np.sum(r_lp))
        groups[(counts_t, s)].append((sum_lp, seed_xy, r_lp))

    # Select top_K per group and forward LP radii as warm starts
    top_K = 3  # Change 1c
    selected_seeds: List[Tuple[np.ndarray, np.ndarray]] = []
    for key, items in groups.items():
        items.sort(key=lambda t: t[0], reverse=True)
        for i in range(min(top_K, len(items))):
            _, xy_seed, r_warm = items[i]
            selected_seeds.append((xy_seed, r_warm))

    # Helper: near-active pairs counter for adaptive cadence
    def near_active_pairs_count(xy: np.ndarray, r: np.ndarray, eps: float, thresh: float = 2e-4) -> int:
        D = pairwise_dists(xy)
        N = len(r)
        cnt = 0
        for i in range(N):
            for j in range(i + 1, N):
                s_ij = D[i, j] - r[i] - r[j] - eps
                if s_ij < thresh:
                    cnt += 1
        return cnt

    # Change 2: growth-and-move with adaptive LP correction cadence
    def growth_and_move_with_adaptive_lp(
        xy: np.ndarray,
        r: np.ndarray,
        mu: float,
        eps: float,
        max_iters: int = 120,
        init_step: float = 0.05,
        shrink: float = 0.5,
        K_init: int = 50,
        B: int = 50,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Barrier ascent with periodic LP reallocation. The LP cadence K is adapted per block."""
        xy = xy.copy()
        r = r.copy()

        # Ensure strict feasibility for starting eps
        r *= 0.999999
        f, gx, gy, gr, ok = barrier_objective_and_grad(xy, r, mu, eps)
        if not ok:
            # Try uniform shrinks
            for _ in range(20):
                r *= 0.9
                f, gx, gy, gr, ok = barrier_objective_and_grad(xy, r, mu, eps)
                if ok:
                    break
            if not ok:
                return xy, r

        step = init_step
        total_iter = 0
        since_lp = 0
        K = K_init
        block_iter = 0
        block_sum_start = float(np.sum(r))
        prev_near_active = near_active_pairs_count(xy, r, eps)
        improved_in_block = False

        while total_iter < max_iters:
            # Gradient normalization
            g_norm = np.sqrt(np.sum(gx * gx) + np.sum(gy * gy) + np.sum(gr * gr))
            if not np.isfinite(g_norm) or g_norm < 1e-12:
                break

            dx = (gx / (g_norm + 1e-18)) * step
            dy = (gy / (g_norm + 1e-18)) * step
            dr = (gr / (g_norm + 1e-18)) * step

            improved = False
            current_xy = xy
            current_r = r
            # Backtracking line-search
            for _bt in range(20):
                trial_xy = np.empty_like(xy)
                trial_xy[:, 0] = current_xy[:, 0] + dx
                trial_xy[:, 1] = current_xy[:, 1] + dy
                trial_r = current_r + dr
                trial_r = np.maximum(trial_r, 1e-12)
                trial_xy[:, 0] = np.clip(trial_xy[:, 0], trial_r + eps, 1.0 - trial_r - eps)
                trial_xy[:, 1] = np.clip(trial_xy[:, 1], trial_r + eps, 1.0 - trial_r - eps)

                f_new, gx_new, gy_new, gr_new, ok_new = barrier_objective_and_grad(trial_xy, trial_r, mu, eps)
                if ok_new and np.isfinite(f_new) and f_new > f + 1e-12:
                    xy = trial_xy
                    r = trial_r
                    f = f_new
                    gx, gy, gr = gx_new, gy_new, gr_new
                    improved = True
                    break
                else:
                    dx *= shrink
                    dy *= shrink
                    dr *= shrink
            if not improved:
                # No further improvement at this mu; break early
                break

            # Update counters
            total_iter += 1
            block_iter += 1
            since_lp += 1

            # Check if time to run LP correction at adaptive cadence
            if since_lp >= K:
                # Fixed-center LP reallocation with acceptance gate
                r_lp, sum_lp, ok_lp = fixed_center_lp_radii(xy, eps=eps)
                sum_r = float(np.sum(r))
                if ok_lp and sum_lp > sum_r + 1e-14:
                    r = r_lp
                    f, gx, gy, gr, ok = barrier_objective_and_grad(xy, r, mu, eps)
                    if not ok:
                        # Should not happen, but ensure feasibility
                        r *= 0.999999
                        f, gx, gy, gr, ok = barrier_objective_and_grad(xy, r, mu, eps)
                    improved_in_block = True
                since_lp = 0  # reset LP counter

            # End of trust-region block: adapt K based on progress and near-active tightening
            if block_iter >= B:
                sum_r_now = float(np.sum(r))
                near_now = near_active_pairs_count(xy, r, eps)
                # Criteria: no improvement in block OR near-active increased by >=10%
                if (sum_r_now <= block_sum_start + 1e-14) or (near_now >= int(np.ceil(1.1 * prev_near_active))):
                    K = max(20, int(np.floor(0.6 * K)))
                else:
                    K = min(60, int(np.ceil(1.1 * K)))
                # Reset block stats
                prev_near_active = near_now
                block_sum_start = sum_r_now
                block_iter = 0
                improved_in_block = False

        return xy, r

    # Pipeline runner with warm-start radii from LP preselection
    def run_pipeline(xy_seed: np.ndarray, r_warm: Optional[np.ndarray] = None) -> Tuple[np.ndarray, np.ndarray, float]:
        eps_init = 1e-6
        eps_mid = 1e-10
        eps_final = 1e-12
        mu_schedule = [5e-2, 2e-2, 1e-2, 5e-3, 3e-3, 1e-3]
        mini_mus = [2e-3, 1e-3, 5e-4]

        xy = xy_seed.copy()

        # Warm-start radii from LP preselection if provided; otherwise start strictly feasible small radii
        if r_warm is not None and len(r_warm) == xy.shape[0]:
            r = np.maximum(r_warm.copy(), 1e-12)
        else:
            r = strictly_feasible_init_radii(xy, eps_init)
        sum_r = float(np.sum(r))

        # Initial LP at eps_init to ensure feasibility under the stage epsilon and capture easy gains
        r_lp, sum_lp, ok = fixed_center_lp_radii(xy, eps=eps_init)
        if ok and sum_lp > sum_r + 1e-14:
            r = r_lp
            sum_r = sum_lp

        # Main growth-and-move with adaptive LP cadence across the mu schedule
        K_cadence = 50  # initial cadence
        for mu in mu_schedule:
            xy, r = growth_and_move_with_adaptive_lp(
                xy, r, mu=mu, eps=eps_init, max_iters=120, init_step=0.05, shrink=0.5, K_init=K_cadence, B=50
            )
            # After each mu stage, try a fixed-center LP to capture remaining slack (acceptance gated)
            r_lp, sum_lp, ok = fixed_center_lp_radii(xy, eps=eps_init)
            if ok and sum_lp > float(np.sum(r)) + 1e-14:
                r = r_lp

        # First centered uniform inflation + LP at eps_init
        xy_inf, r_inf, t1 = centered_uniform_inflation(xy, r, eps=eps_init)
        if t1 > 1.0 + 1e-12:
            r_lp, sum_lp, ok = fixed_center_lp_radii(xy_inf, eps=eps_init)
            sum_inf = float(np.sum(r_inf))
            if ok and sum_lp > sum_inf + 1e-14:
                xy, r = xy_inf, r_lp
            elif sum_inf > float(np.sum(r)) + 1e-14:
                xy, r = xy_inf, r_inf

        # Endgame LP–mini-barrier–LP sandwich with annealed epsilon (unchanged)
        r *= 0.999999
        r_lp, sum_lp, ok = fixed_center_lp_radii(xy, eps=eps_mid)
        if ok and sum_lp > float(np.sum(r)) + 1e-14:
            r = r_lp

        r *= 0.999999
        for mu in mini_mus:
            xy, r = backtracking_barrier_ascent(xy, r, mu=mu, eps=eps_mid, max_iters=80, init_step=0.03)

        r *= 0.999999
        r_lp, sum_lp, ok = fixed_center_lp_radii(xy, eps=eps_final)
        if ok and sum_lp > float(np.sum(r)) + 1e-14:
            r = r_lp

        # Second centered uniform inflation + LP at eps_final
        xy_inf2, r_inf2, t2 = centered_uniform_inflation(xy, r, eps=eps_final)
        if t2 > 1.0 + 1e-12:
            r_lp, sum_lp, ok = fixed_center_lp_radii(xy_inf2, eps=eps_final)
            sum_inf2 = float(np.sum(r_inf2))
            if ok and sum_lp > sum_inf2 + 1e-14:
                xy, r = xy_inf2, r_lp
            elif sum_inf2 > float(np.sum(r)) + 1e-14:
                xy, r = xy_inf2, r_inf2

        # Final strict feasibility polish
        xy, r = enforce_strict_feasibility(xy, r, eps_final)
        r = overlap_polish(xy, r, eps=eps_final, max_iters=200)
        return xy, r, float(np.sum(r))

    # Run pipeline for each selected survivor and keep the best
    best_sum = -1.0
    best_xy = None
    best_r = None
    for xy_seed, r_warm in selected_seeds:
        xy, r, sum_r = run_pipeline(xy_seed, r_warm=r_warm)
        if sum_r > best_sum + 1e-14:
            best_sum = sum_r
            best_xy = xy
            best_r = r

    assert best_xy is not None and best_r is not None
    circles = np.column_stack((best_xy, best_r))
    return circles
# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```

```python
#!/usr/bin/env python3
"""High-performance candidate for packing 21 circles in a perimeter-four rectangle.

Implements the algorithm described in the prompt:

- Packs inside the unit square (W + H = 2), ensuring the rectangle perimeter ≤ 4.
- Multi-start hexagonal seeding with several 5-row patterns that sum to 21, plus anisotropic "aspect-crawl" variants.
- Fixed-center LP allocation (exact via SciPy HiGHS when available) for radii, with safe fallback.
- Trust-region growth-and-move stage with periodic LP reallocations.
- Barrier ascent using a smooth interior log-barrier on slacks with lazy augmentation of pairwise constraints.
- Periodic LP inflations to reallocate slack created by center moves.
- Dual uniform inflations about the box center to convert boundary slack into larger radii.
- Epsilon annealing schedule to approach near-tangency safely.
- Endgame LP–mini-barrier–LP sandwich with monotone acceptance gates and an optional short dense barrier squeeze.
- Soft anchors (final barrier passes) gently pull two bottom-edge circles toward y ≈ r to eliminate shear/rotation.
- Strict-feasibility polish ensuring all slacks are > 0 and a tiny final global shrink of radii.

Monotone acceptance gates are used: whenever we consider adopting a new allocation of radii,
we require a strict increase in Σ r before committing.

Default behavior works well for N = 21.
"""

import json
import math
from typing import List, Tuple, Set

import numpy as np


# EVOLVE_START
def construct_packing(num_circles: int = 21):
    """
    Main entry: construct 21 disjoint circles within the unit square [0,1]^2.

    Returns an array of shape (21, 3) with rows [x, y, r].
    """

    # Parameters
    assert num_circles == 21, "This solver is specialized for 21 circles."
    epsilon_schedule = [1e-6, 5e-7, 1e-7, 1e-8, 1e-10, 1e-12]
    top_K = 3  # keep only top K seeds by preselection Σ r
    jitter_low, jitter_high = 1e-3, 2e-3  # jitter range for seed diversification

    # Hex row patterns summing to 21 (expanded set)
    row_patterns = [
        [5, 4, 4, 4, 4],
        [4, 5, 4, 4, 4],
        [4, 4, 5, 4, 4],
        [4, 4, 4, 5, 4],
        [5, 4, 5, 4, 3],
        [5, 5, 4, 4, 3],
        [4, 5, 5, 4, 3],
    ]
    spacing_scales = [0.98, 1.00, 1.02]

    # Generate seeds with anisotropic aspect-crawl variants and jittered copies
    seeds = []
    for pattern in row_patterns:
        for scale in spacing_scales:
            centers = seed_hex_pattern(pattern, scale=scale)  # base hex pattern
            # Include the base centers
            seeds.append(np.clip(centers.copy(), 0.0, 1.0))
            # Add two anisotropic variants (±3% with opposite signs)
            for ax, ay in [(1.03, 0.97), (0.97, 1.03)]:
                var = anisotropic_scale(centers, ax=ax, ay=ay)
                seeds.append(np.clip(var.copy(), 0.0, 1.0))
            # For base + each anisotropic variant, create two jittered copies
            base_and_vars = [centers] + [anisotropic_scale(centers, ax=ax, ay=ay) for ax, ay in [(1.03, 0.97), (0.97, 1.03)]]
            for C in base_and_vars:
                for _ in range(2):
                    mag = np.random.uniform(jitter_low, jitter_high)
                    c = C.copy()
                    c += np.random.uniform(-mag, mag, size=c.shape)
                    np.clip(c, 0.0, 1.0, out=c)
                    seeds.append(c)

    # Preselection: fixed-center LP inflation (exact when SciPy available) for each seed
    preselected = []
    for centers in seeds:
        r = fixed_center_inflate(centers, np.zeros(len(centers)), epsilon_schedule[0])
        preselected.append((np.sum(r), centers, r))
    preselected.sort(key=lambda t: t[0], reverse=True)

    # Keep only top K seeds
    candidates = preselected[:top_K]

    # Optimize each candidate via trust-region + barrier + LP + uniform inflations and annealing
    best_sum = -np.inf
    best_xy = None
    best_r = None

    for _, centers, r_init in candidates:
        x = centers[:, 0].copy()
        y = centers[:, 1].copy()
        r = r_init.copy()

        # Initial trust-region growth-and-move stage at the initial epsilon
        eps0 = epsilon_schedule[0]
        x, y, r = trust_region_optimize(x, y, r, eps=eps0, iterations=360, block_size=50, beta=0.4, near_thresh=0.03)

        # A quick LP reallocation after trust-region stage
        r_new = fixed_center_inflate(np.column_stack([x, y]), r, eps0)
        if np.sum(r_new) > np.sum(r) + 1e-14:
            r = r_new

        # Initial sparse neighbor graph (k-NN)
        pairs = initial_neighbor_pairs(x, y, k=min(6, len(x) - 1))

        # Barrier ascent across epsilon schedule with periodic LP and uniform inflations
        mid_uniform_done = False
        for stage, eps in enumerate(epsilon_schedule):
            # Barrier stage with lazy augmentation
            x, y, r, pairs = barrier_ascent(x, y, r, eps, pairs, mu=1e-3, iterations=30)

            # Periodic fixed-center LP inflation (accept only if Σ r increases)
            r_new = fixed_center_inflate(np.column_stack([x, y]), r, eps)
            if np.sum(r_new) > np.sum(r) + 1e-14:
                r = r_new

            # Global uniform inflation once mid-run, immediately followed by LP capture
            if not mid_uniform_done and stage == max(1, len(epsilon_schedule) // 2 - 1):
                x2, y2, r2 = uniform_inflation(x, y, r, eps)
                if np.sum(r2) > np.sum(r) + 1e-14:
                    x, y, r = x2, y2, r2
                    # LP after uniform inflation
                    r_new = fixed_center_inflate(np.column_stack([x, y]), r, eps)
                    if np.sum(r_new) > np.sum(r) + 1e-14:
                        r = r_new
                mid_uniform_done = True

        # Near-end uniform inflation, then LP
        eps_final = epsilon_schedule[-1]
        x2, y2, r2 = uniform_inflation(x, y, r, eps_final)
        if np.sum(r2) > np.sum(r) + 1e-14:
            x, y, r = x2, y2, r2
            r_new = fixed_center_inflate(np.column_stack([x, y]), r, eps_final)
            if np.sum(r_new) > np.sum(r) + 1e-14:
                r = r_new

        # Endgame LP–mini-barrier–LP sandwich, with soft anchors and optional dense squeeze
        r1 = fixed_center_inflate(np.column_stack([x, y]), r, eps_final)
        if np.sum(r1) > np.sum(r) + 1e-14:
            r = r1

        # Sparse barrier passes with decreasing μ, soft anchors on two bottom-edge circles
        for mu_end in [5e-4, 2e-4, 1e-4]:
            x, y, r, pairs = barrier_ascent(x, y, r, eps_final, pairs, mu=mu_end, iterations=20, soft_anchor=True, anchor_strength=mu_end)

            r2 = fixed_center_inflate(np.column_stack([x, y]), r, eps_final)
            if np.sum(r2) > np.sum(r) + 1e-14:
                r = r2

        # Optional short dense mini-barrier (all pairs) to squeeze residual structured slack
        dense_pairs = all_pairs(len(r))
        x, y, r, _ = barrier_ascent(x, y, r, eps_final, dense_pairs, mu=1e-4, iterations=12, soft_anchor=True, anchor_strength=1e-4)
        r3 = fixed_center_inflate(np.column_stack([x, y]), r, eps_final)
        if np.sum(r3) > np.sum(r) + 1e-14:
            r = r3

        # Strict feasibility polish (includes tiny global shrink)
        x, y, r = strict_feasibility_polish(x, y, r, eps_final)

        # Update best
        total_r = np.sum(r)
        if total_r > best_sum:
            best_sum = total_r
            best_xy = np.column_stack([x, y])
            best_r = r

    circles = np.column_stack([best_xy, best_r])
    return circles


# ------------------------------
# Seeding: true hex staggering
# ------------------------------
def seed_hex_pattern(row_counts: List[int], scale: float = 1.0) -> np.ndarray:
    """
    Generate centers using hexagonal staggering for a given row-count pattern.

    - row_counts: list like [5,4,4,4,4] summing to 21
    - scale: spacing multiplier around base s ≈ 0.20

    Returns an array of shape (21,2).
    """
    R = len(row_counts)
    assert sum(row_counts) == 21, "Row pattern must sum to 21."
    max_cols = max(row_counts)

    # Base horizontal spacing around 0.20 as specified; perturb by 'scale'
    s = 0.20 * scale
    dv = s * math.sqrt(3.0) / 2.0

    # Compute y-coordinates centered around 0.5
    y0 = 0.5 - dv * (R - 1) / 2.0
    ys = np.array([y0 + r * dv for r in range(R)], dtype=float)

    centers = []
    for r_idx, n in enumerate(row_counts):
        # Row width based on fixed spacing s
        row_width = (n - 1) * s if n > 1 else 0.0
        x_center = 0.5
        # Hex staggering offset: half-spacing for odd rows
        offset = (0.5 * s) if (r_idx % 2 == 1) else 0.0

        x_start = x_center - row_width / 2.0 + offset
        xs = [x_start + c * s for c in range(n)]
        for x in xs:
            centers.append([x, ys[r_idx]])

    centers = np.array(centers, dtype=float)
    # Clip within [0,1] in case of small overflow
    np.clip(centers, 0.0, 1.0, out=centers)
    return centers


def anisotropic_scale(centers: np.ndarray, ax: float, ay: float) -> np.ndarray:
    """
    Scale centers anisotropically about (0.5, 0.5): x' = 0.5 + ax * (x - 0.5), y' = 0.5 + ay * (y - 0.5)
    """
    c = centers.copy()
    c[:, 0] = 0.5 + ax * (c[:, 0] - 0.5)
    c[:, 1] = 0.5 + ay * (c[:, 1] - 0.5)
    np.clip(c, 0.0, 1.0, out=c)
    return c


# -------------------------------------------------
# Exact fixed-center LP inflation (SciPy, optional)
# -------------------------------------------------
def exact_lp_inflate(centers: np.ndarray, eps: float) -> np.ndarray:
    """
    Solve the exact fixed-center LP:
        maximize sum(r_i)
        subject to 0 <= r_i <= m_i and r_i + r_j <= d_ij - eps for all i<j

    - m_i is the wall-limited maximum radius (computed with given eps to keep strict wall slack).
    - eps is the clearance used in pair constraints; callers pass eps_lp = min(stage_eps, 1e-10).

    Returns:
        r* (np.ndarray) if SciPy is available and LP succeeds; otherwise returns None.
        The returned solution may be right at tangency under eps, so subsequent safeguards
        will clip/repair before adoption.
    """
    try:
        from scipy.optimize import linprog
    except Exception:
        return None

    n = centers.shape[0]
    x = centers[:, 0]
    y = centers[:, 1]

    # Wall bounds using eps margin to preserve strict feasibility downstream.
    m = np.minimum.reduce([x - eps, 1.0 - x - eps, y - eps, 1.0 - y - eps])
    m = np.maximum(m, 0.0)

    # Pairwise constraints matrix A_ub and vector b_ub
    dx = x.reshape(-1, 1) - x.reshape(1, -1)
    dy = y.reshape(-1, 1) - y.reshape(1, -1)
    dij = np.sqrt(dx * dx + dy * dy)

    num_pairs = n * (n - 1) // 2
    A_ub = np.zeros((num_pairs, n), dtype=float)
    b_ub = np.zeros(num_pairs, dtype=float)

    k = 0
    for i in range(n):
        for j in range(i + 1, n):
            A_ub[k, i] = 1.0
            A_ub[k, j] = 1.0
            b_ub[k] = dij[i, j] - eps
            k += 1

    # Linear objective: minimize -sum(r) => maximize sum(r)
    c = -np.ones(n, dtype=float)
    bounds = [(0.0, float(m[i])) for i in range(n)]

    # Solve using HiGHS
    res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
    if res.success and res.x is not None:
        return res.x.astype(float)
    return None


# ------------------------------------------
# Fixed-center LP-like inflation with LP hook
# ------------------------------------------
def fixed_center_inflate(centers: np.ndarray, r0: np.ndarray, eps: float) -> np.ndarray:
    """
    Freeze centers and allocate radii to maximize sum r under walls and pairwise constraints.

    When SciPy is available, first try the exact LP with a tight pair clearance
    eps_lp = min(eps, 1e-10). If the LP returns a solution that strictly increases Σ r
    over the current radii, adopt it (after enforcing feasibility with the stage eps).
    Otherwise, fall back to the greedy water-filling heuristic.

    All adoptions are guarded by a monotone acceptance gate.
    """
    n = len(centers)
    x = centers[:, 0]
    y = centers[:, 1]
    r_current = r0.copy()

    # Wall bounds computed with the stage epsilon to maintain strict feasibility.
    m_eps = np.minimum.reduce([x - eps, 1.0 - x - eps, y - eps, 1.0 - y - eps])
    m_eps = np.maximum(m_eps, 0.0)

    # Clip current radii to be within wall bounds
    r_current = np.minimum(np.maximum(r_current, 0.0), m_eps)

    # Attempt exact LP first with tighter pair clearance
    eps_lp = min(eps, 1e-10)
    r_lp = exact_lp_inflate(centers, eps_lp)

    if r_lp is not None:
        # Enforce feasibility under stage eps:
        # - clip to wall bounds under eps
        # - repair any pairwise overlaps at eps by reducing larger radii
        r_candidate = np.minimum(np.maximum(r_lp, 0.0), m_eps)
        r_candidate = repair_pairwise_overlaps(x, y, r_candidate, eps, passes=60)

        # Monotone acceptance gate
        if np.sum(r_candidate) > np.sum(r_current) + 1e-12:
            return r_candidate

    # Fallback: greedy water-filling heuristic (original approach)
    r = r_current.copy()

    # Initial increments δ = m - r
    delta = m_eps - r
    delta = np.maximum(delta, 0.0)

    # Precompute pair distances
    dx = x.reshape(-1, 1) - x.reshape(1, -1)
    dy = y.reshape(-1, 1) - y.reshape(1, -1)
    dij = np.sqrt(dx * dx + dy * dy)

    # s_ij current slack for increments
    s_ij = dij - (r.reshape(-1, 1) + r.reshape(1, -1)) - eps

    # Iterate greedy passes controlling pair increments
    for _ in range(120):
        violated = 0
        for i in range(n):
            for j in range(i + 1, n):
                cap = s_ij[i, j]
                if cap < 0.0:
                    cap = 0.0
                if delta[i] + delta[j] > cap:
                    excess = delta[i] + delta[j] - cap
                    if delta[i] >= delta[j]:
                        delta[i] -= excess
                        if delta[i] < 0.0:
                            delta[i] = 0.0
                    else:
                        delta[j] -= excess
                        if delta[j] < 0.0:
                            delta[j] = 0.0
                    violated += 1
        if violated == 0:
            break

    r_new = r + delta
    r_new = np.minimum(r_new, m_eps)
    r_new = np.maximum(r_new, 0.0)
    return r_new


def repair_pairwise_overlaps(x: np.ndarray, y: np.ndarray, r: np.ndarray, eps: float, passes: int = 60) -> np.ndarray:
    """
    Enforce pairwise feasibility under clearance eps by reducing the larger radius
    of any violating pair by half the violation. Centers remain fixed.

    This is a local repair used after LP adoption to ensure strict feasibility
    w.r.t. the current stage epsilon, without moving centers.
    """
    n = len(r)
    pts = np.column_stack([x, y])
    r = r.copy()

    for _ in range(passes):
        fixed = 0
        for i in range(n):
            for j in range(i + 1, n):
                dij = float(np.linalg.norm(pts[i] - pts[j]))
                slack = dij - (r[i] + r[j]) - eps
                if slack < 0.0:
                    excess = -slack
                    if r[i] >= r[j]:
                        r[i] = max(r[i] - 0.5 * excess, 0.0)
                    else:
                        r[j] = max(r[j] - 0.5 * excess, 0.0)
                    fixed += 1
        if fixed == 0:
            break
    return r


# --------------------------
# Trust-region growth-and-move
# --------------------------
def trust_region_optimize(
    x: np.ndarray,
    y: np.ndarray,
    r: np.ndarray,
    eps: float,
    iterations: int = 360,
    block_size: int = 50,
    beta: float = 0.4,
    near_thresh: float = 0.03,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Trust-region growth-and-move stage:
    - Coordinate-ascent on radii consuming a fraction β of limiting slack (walls and nearest neighbor)
    - Gentle center moves driven by near-active wall and pair slacks
    - Adaptive trust radius: grows when blocks improve Σ r, shrinks otherwise
    - Adaptive fixed-center LP cadence: invoke LP every K iterations where K is updated after each block.
      K tightens when constraints are tightening or progress stalls, and relaxes when progress is smooth.
    - After each block: project to feasibility, quick overlap repair, and adjust trust radius.
    """

    def compute_near_active_counts(xx: np.ndarray, yy: np.ndarray, rr: np.ndarray, tau: float) -> Tuple[int, int]:
        """
        Compute:
          - S_pair: number of (i,j), i<j with d_ij - (r_i + r_j) < tau
          - S_wall: number of i with min wall slack (no eps) < tau
        """
        nloc = len(rr)
        # Pair near-actives
        dx = xx.reshape(-1, 1) - xx.reshape(1, -1)
        dy = yy.reshape(-1, 1) - yy.reshape(1, -1)
        dij = np.sqrt(dx * dx + dy * dy)
        pair_gap = dij - (rr.reshape(-1, 1) + rr.reshape(1, -1))
        # count only i<j
        iu = np.triu_indices(nloc, k=1)
        S_pair = int(np.sum(pair_gap[iu] < tau))

        # Wall near-actives (no eps in definition)
        s_left = xx - rr
        s_right = 1.0 - xx - rr
        s_bottom = yy - rr
        s_top = 1.0 - yy - rr
        s_wall_min = np.minimum.reduce([s_left, s_right, s_bottom, s_top])
        S_wall = int(np.sum(s_wall_min < tau))
        return S_pair, S_wall

    n = len(r)
    tr = 0.02  # initial trust radius for center moves
    tr_min, tr_max = 0.002, 0.06

    # Adaptive LP cadence parameters
    K = int(block_size)  # initialize to current default (e.g., 50)
    since_lp = 0
    tau = 0.5 * near_thresh  # near-active threshold for cadence controller
    # Initial near-active counts
    last_S_pair, last_S_wall = compute_near_active_counts(x, y, r, tau)
    last_S = last_S_pair + last_S_wall

    # Block tracking
    block_iter = 0
    block_sum_start = float(np.sum(r))

    for it in range(iterations):
        # Compute wall slacks (with eps)
        s_left = x - r - eps
        s_right = 1.0 - x - r - eps
        s_bottom = y - r - eps
        s_top = 1.0 - y - r - eps
        s_wall = np.minimum.reduce([s_left, s_right, s_bottom, s_top])

        # Compute pair slacks matrix and per-circle min pair slack (with eps)
        S = pair_slack_matrix(x, y, r, eps)
        # fill diagonal with +inf so min over row/col excludes self
        np.fill_diagonal(S, np.inf)
        s_pair_min = np.min(S, axis=1)

        # Radii increment limited by both walls and nearest neighbors
        avail = np.maximum(0.0, np.minimum(s_wall, s_pair_min))
        delta_r = beta * avail
        r = r + delta_r

        # Gentle center moves based on near-active constraints
        dx = np.zeros(n, dtype=float)
        dy = np.zeros(n, dtype=float)

        # Wall pushes
        def wall_weight(sl):
            return np.clip((near_thresh - sl) / max(near_thresh, 1e-12), 0.0, 1.0)

        wx = wall_weight(s_left) - wall_weight(s_right)
        wy = wall_weight(s_bottom) - wall_weight(s_top)

        dx += tr * wx
        dy += tr * wy

        # Pair pushes (symmetric)
        for i in range(n):
            for j in range(i + 1, n):
                sij = S[i, j]
                if not np.isfinite(sij):
                    continue
                if sij < near_thresh:
                    # push apart along the unit vector
                    vx = x[i] - x[j]
                    vy = y[i] - y[j]
                    dij = math.hypot(vx, vy)
                    if dij < 1e-12:
                        # random tiny nudge if coincident
                        ux, uy = 1.0, 0.0
                    else:
                        ux, uy = vx / dij, vy / dij
                    w = (near_thresh - sij) / max(near_thresh, 1e-12)
                    step = 0.5 * tr * w
                    dx[i] += step * ux
                    dy[i] += step * uy
                    dx[j] -= step * ux
                    dy[j] -= step * uy

        # Cap per-circle move to trust radius
        move_norm = np.hypot(dx, dy)
        scale = np.ones_like(move_norm)
        over = move_norm > tr
        scale[over] = tr / (move_norm[over] + 1e-18)
        dx *= scale
        dy *= scale

        # Apply moves and keep inside [0,1]
        x = np.clip(x + dx, 0.0, 1.0)
        y = np.clip(y + dy, 0.0, 1.0)

        # Periodic LP reallocation based on adaptive cadence K
        since_lp += 1
        block_iter += 1
        if since_lp >= K:
            # "Immediately reproject and polish as before" around LP invocation:
            # - Clip radii to wall bounds
            m_eps = np.minimum.reduce([x - eps, 1.0 - x - eps, y - eps, 1.0 - y - eps])
            m_eps = np.maximum(m_eps, 0.0)
            r = np.minimum(r, m_eps)

            # - Keep centers strictly within walls
            x = np.minimum(np.maximum(x, r + eps), 1.0 - r - eps)
            y = np.minimum(np.maximum(y, r + eps), 1.0 - r - eps)

            # - Quick overlap repair
            r = repair_pairwise_overlaps(x, y, r, eps, passes=20)

            # LP reallocation with monotone acceptance gate
            r_lp = fixed_center_inflate(np.column_stack([x, y]), r, eps)
            if np.sum(r_lp) > np.sum(r) + 1e-14:
                r = r_lp
                # Post-adoption polish: ensure strict walls and fix any minor overlap numerics
                x = np.minimum(np.maximum(x, r + eps), 1.0 - r - eps)
                y = np.minimum(np.maximum(y, r + eps), 1.0 - r - eps)
                r = repair_pairwise_overlaps(x, y, r, eps, passes=15)

            since_lp = 0  # reset cadence counter

        # End of block: project/repair, adjust trust radius, and adapt LP cadence K
        if block_iter >= block_size or it == iterations - 1:
            # Feasibility projection (as before)
            m_eps = np.minimum.reduce([x - eps, 1.0 - x - eps, y - eps, 1.0 - y - eps])
            m_eps = np.maximum(m_eps, 0.0)
            r = np.minimum(r, m_eps)

            x = np.minimum(np.maximum(x, r + eps), 1.0 - r - eps)
            y = np.minimum(np.maximum(y, r + eps), 1.0 - r - eps)

            r = repair_pairwise_overlaps(x, y, r, eps, passes=30)

            # Trust radius adaptation uses block improvement
            block_sum_end = float(np.sum(r))
            if block_sum_end > block_sum_start + 1e-12:
                tr = min(tr * 1.2, tr_max)
            else:
                tr = max(tr * 0.7, tr_min)

            # Compute near-active indicators and adapt LP period K
            S_pair, S_wall = compute_near_active_counts(x, y, r, tau)
            S_total = S_pair + S_wall
            tighten_constraints = S_total >= 1.1 * max(1, last_S)
            no_improve = block_sum_end <= block_sum_start + 1e-12

            if no_improve or tighten_constraints:
                K = max(20, int(math.floor(0.6 * K)))
            else:
                K = min(60, int(math.ceil(1.1 * K)))

            # Update trackers for next block
            last_S = S_total
            block_sum_start = block_sum_end
            block_iter = 0

    return x, y, r


def pair_slack_matrix(x: np.ndarray, y: np.ndarray, r: np.ndarray, eps: float) -> np.ndarray:
    """
    Compute pair slack matrix S, where S[i,j] = ||p_i - p_j|| - (r_i + r_j) - eps for i != j,
    and S[i,i] is left as 0 (will be set to inf by caller when taking mins).
    """
    dx = x.reshape(-1, 1) - x.reshape(1, -1)
    dy = y.reshape(-1, 1) - y.reshape(1, -1)
    dij = np.sqrt(dx * dx + dy * dy)
    S = dij - (r.reshape(-1, 1) + r.reshape(1, -1)) - eps
    return S


# --------------------------
# Neighbor pairs (sparse)
# --------------------------
def initial_neighbor_pairs(x: np.ndarray, y: np.ndarray, k: int = 6) -> Set[Tuple[int, int]]:
    """
    Construct a sparse neighbor graph via k-NN (undirected pairs).
    """
    n = len(x)
    pairs = set()
    pts = np.column_stack([x, y])
    for i in range(n):
        d = np.sqrt(np.sum((pts - pts[i]) ** 2, axis=1))
        idx = np.argsort(d)
        for j in idx[1 : k + 1]:  # skip self
            a, b = (i, j) if i < j else (j, i)
            pairs.add((a, b))
    return pairs


def all_pairs(n: int) -> Set[Tuple[int, int]]:
    """
    Return the full set of all unordered pairs (i, j), i < j.
    """
    return {(i, j) for i in range(n) for j in range(i + 1, n)}


# ---------------------------------
# Barrier ascent with lazy pairs
# ---------------------------------
def barrier_ascent(
    x: np.ndarray,
    y: np.ndarray,
    r: np.ndarray,
    eps: float,
    pairs: Set[Tuple[int, int]],
    mu: float = 1e-3,
    iterations: int = 30,
    soft_anchor: bool = False,
    anchor_strength: float = 0.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Set[Tuple[int, int]]]:
    """
    Interior-point barrier ascent for maximizing sum r + μ sum log(slack), with lazy pair augmentation.

    - Starts with a sparse neighbor graph 'pairs' (or dense, if provided)
    - Adds violated non-neighbor pairs on demand
    - Uses backtracking line search with strict slack positivity
    - Optional soft anchors: mild quadratic pulls for two bottom-edge circles toward y ≈ r
      to reduce shear/rotation artifacts in the endgame (applied via a small anchor_strength)
    """
    n = len(r)

    def barrier_objective(xx, yy, rr, pair_set) -> float:
        s = compute_slacks(xx, yy, rr, eps, pair_set)
        # If any slack nonpositive, return very low objective
        if np.any(s["pos"] <= 0.0) or np.any(s["left"] <= 0.0) or np.any(s["right"] <= 0.0) or np.any(
            s["bottom"] <= 0.0
        ) or np.any(s["top"] <= 0.0):
            return -1e50
        if len(s["pair"]) > 0 and np.any(s["pair"] <= 0.0):
            return -1e50
        # Sum of radii + barrier
        val = np.sum(rr)
        # Wall and positivity
        val += mu * (
            np.sum(np.log(s["pos"]))
            + np.sum(np.log(s["left"]))
            + np.sum(np.log(s["right"]))
            + np.sum(np.log(s["bottom"]))
            + np.sum(np.log(s["top"]))
        )
        # Pair slacks
        if len(s["pair"]) > 0:
            val += mu * np.sum(np.log(s["pair"]))

        # Soft anchor penalty: subtract small quadratic on bottom slack for two most bottom-edge circles
        if soft_anchor and anchor_strength > 0.0:
            # bottom slack target ~ 0 -> penalize deviation (y - r - eps)
            s_bottom = yy - rr - eps
            # pick two indices with smallest y (or smallest bottom slack)
            # Using smallest y tends to anchor true bottom-edge circles
            idx = np.argsort(yy)[:2]
            penalty = 0.5 * anchor_strength * np.sum((s_bottom[idx]) ** 2)
            val -= penalty

        return float(val)

    def barrier_grad(xx, yy, rr, pair_set):
        # Compute slacks and their gradients
        s = compute_slacks(xx, yy, rr, eps, pair_set)

        # Gradients initialized
        gx = np.zeros(n, dtype=float)
        gy = np.zeros(n, dtype=float)
        gr = np.ones(n, dtype=float)  # from Σ r

        # Positivity r > eps -> s_pos = r - eps
        gr += mu * (1.0 / s["pos"])

        # Walls: s_left = x - r - eps
        gx += mu * (1.0 / s["left"])
        gr -= mu * (1.0 / s["left"])

        # s_right = 1 - x - r - eps
        gx -= mu * (1.0 / s["right"])
        gr -= mu * (1.0 / s["right"])

        # s_bottom = y - r - eps
        gy += mu * (1.0 / s["bottom"])
        gr -= mu * (1.0 / s["bottom"])

        # s_top = 1 - y - r - eps
        gy -= mu * (1.0 / s["top"])
        gr -= mu * (1.0 / s["top"])

        # Pair slacks: s_ij = ||p_i - p_j|| - (r_i + r_j) - eps
        # Grad contribution: μ * (∂d/∂x_i)/s_ij for x, similar y; and -μ/s_ij for r_i and r_j
        if len(s["pair"]) > 0:
            # Prepare pair distance diffs
            idx_i = s["pair_idx"][:, 0]
            idx_j = s["pair_idx"][:, 1]
            dx = xx[idx_i] - xx[idx_j]
            dy = yy[idx_i] - yy[idx_j]
            dij = np.sqrt(dx * dx + dy * dy) + 1e-18  # avoid divide by zero
            coeff = mu / s["pair"]

            # x,y contributions
            contrib_x_i = coeff * (dx / dij)
            contrib_x_j = -contrib_x_i
            gx = accumulate_pair_contrib(gx, idx_i, contrib_x_i)
            gx = accumulate_pair_contrib(gx, idx_j, contrib_x_j)

            contrib_y_i = coeff * (dy / dij)
            contrib_y_j = -contrib_y_i
            gy = accumulate_pair_contrib(gy, idx_i, contrib_y_i)
            gy = accumulate_pair_contrib(gy, idx_j, contrib_y_j)

            # r contributions (-μ/s_ij for both i and j)
            rr_contrib = -coeff
            gr = accumulate_pair_contrib(gr, idx_i, rr_contrib)
            gr = accumulate_pair_contrib(gr, idx_j, rr_contrib)

        # Soft anchors: add gradient of -0.5*k*(y_i - r_i - eps)^2 for two bottom-edge circles
        if soft_anchor and anchor_strength > 0.0:
            s_bottom = yy - rr - eps
            idx = np.argsort(yy)[:2]
            # d/dy: -k*(y - r - eps); d/dr: +k*(y - r - eps)
            gy[idx] -= anchor_strength * s_bottom[idx]
            gr[idx] += anchor_strength * s_bottom[idx]

        return gx, gy, gr

    # Backtracking line search parameters
    for it in range(iterations):
        # Lazy augmentation: add violated pairs not yet in constraint set
        pairs = lazy_augment_pairs(x, y, r, eps, pairs, threshold=2.0 * eps)

        # Safety shrink if any slack too small
        shrink_needed = False
        sl = compute_slacks(x, y, r, eps, pairs)
        min_slack = min(
            float(np.min(sl["pos"])),
            float(np.min(sl["left"])),
            float(np.min(sl["right"])),
            float(np.min(sl["bottom"])),
            float(np.min(sl["top"])),
            float(np.min(sl["pair"])) if len(sl["pair"]) > 0 else 1.0,
        )
        if min_slack < 1e-12:
            shrink_needed = True
        if shrink_needed:
            r *= 0.999999  # tiny shrink to maintain strictly positive slacks

        # Compute gradient
        gx, gy, gr = barrier_grad(x, y, r, pairs)
        grad_norm = max(1e-12, np.linalg.norm(np.concatenate([gx, gy, gr])))

        # Take a step along ascent direction
        alpha = 0.05
        obj0 = barrier_objective(x, y, r, pairs)

        # Normalize gradient for stable steps
        dir_x = gx / grad_norm
        dir_y = gy / grad_norm
        dir_r = gr / grad_norm

        # Backtracking to maintain positive slacks and increase objective
        accepted = False
        for _ in range(25):
            xn = x + alpha * dir_x
            yn = y + alpha * dir_y
            rn = r + alpha * dir_r

            # Ensure r stays positive
            rn = np.maximum(rn, eps * 1.001)

            # Check slacks
            s = compute_slacks(xn, yn, rn, eps, pairs)
            if (
                np.all(s["pos"] > 0.0)
                and np.all(s["left"] > 0.0)
                and np.all(s["right"] > 0.0)
                and np.all(s["bottom"] > 0.0)
                and np.all(s["top"] > 0.0)
                and (len(s["pair"]) == 0 or np.all(s["pair"] > 0.0))
            ):
                obj1 = barrier_objective(xn, yn, rn, pairs)
                if obj1 > obj0:
                    x, y, r = xn, yn, rn
                    accepted = True
                    break
            alpha *= 0.5
        if not accepted:
            # If no acceptable step found, stop early
            break

    return x, y, r, pairs


def accumulate_pair_contrib(acc: np.ndarray, idx: np.ndarray, contrib: np.ndarray) -> np.ndarray:
    """
    Add pairwise contributions to accumulator (vectorized scatter-add).
    """
    np.add.at(acc, idx, contrib)
    return acc


def compute_slacks(x: np.ndarray, y: np.ndarray, r: np.ndarray, eps: float, pairs: Set[Tuple[int, int]]):
    """
    Compute all slacks needed for the barrier method and feasibility checks.
    """
    s_pos = r - eps
    s_left = x - r - eps
    s_right = 1.0 - x - r - eps
    s_bottom = y - r - eps
    s_top = 1.0 - y - r - eps

    s_pair = np.array([], dtype=float)
    pair_idx = np.empty((0, 2), dtype=int)
    if len(pairs) > 0:
        idx = np.array(list(pairs), dtype=int)
        pair_idx = idx
        xi = x[idx[:, 0]]
        yi = y[idx[:, 0]]
        xj = x[idx[:, 1]]
        yj = y[idx[:, 1]]
        dij = np.sqrt((xi - xj) ** 2 + (yi - yj) ** 2)
        s_pair = dij - (r[idx[:, 0]] + r[idx[:, 1]]) - eps

    return {
        "pos": s_pos,
        "left": s_left,
        "right": s_right,
        "bottom": s_bottom,
        "top": s_top,
        "pair": s_pair,
        "pair_idx": pair_idx,
    }


def lazy_augment_pairs(
    x: np.ndarray, y: np.ndarray, r: np.ndarray, eps: float, pairs: Set[Tuple[int, int]], threshold: float = 1e-6
) -> Set[Tuple[int, int]]:
    """
    Add any violated non-neighbor pairs on demand to keep problem exact at termination.
    """
    n = len(r)
    all_pairs = pairs.copy()
    # Build a boolean matrix for existing pairs
    present = np.zeros((n, n), dtype=bool)
    for (i, j) in pairs:
        present[i, j] = True
        present[j, i] = True

    # Check all non-existing pairs for violations
    pts = np.column_stack([x, y])
    for i in range(n):
        for j in range(i + 1, n):
            if present[i, j]:
                continue
            dij = np.linalg.norm(pts[i] - pts[j])
            slack = dij - (r[i] + r[j]) - eps
            if slack < threshold:
                all_pairs.add((i, j))
                present[i, j] = present[j, i] = True

    return all_pairs


# -------------------------------
# Global uniform inflation
# -------------------------------
def uniform_inflation(x: np.ndarray, y: np.ndarray, r: np.ndarray, eps: float):
    """
    Uniformly scale centers and radii about the box center (0.5, 0.5) by maximal t ≥ 1
    such that wall constraints remain nonnegative. Pairwise slacks increase under uniform scaling.

    Returns updated (x, y, r); if no inflation possible, returns original.
    """
    cx, cy = 0.5, 0.5
    n = len(r)

    # Compute upper bounds on t from each wall constraint
    t_candidates = [np.inf]

    for i in range(n):
        a_left = (x[i] - cx) - r[i]
        a_right = (x[i] - cx) + r[i]
        a_bottom = (y[i] - cy) - r[i]
        a_top = (y[i] - cy) + r[i]

        # Left wall: cx + t * a_left >= eps -> if a_left < 0 => t <= (cx - eps)/(-a_left)
        if a_left < 0.0:
            t_candidates.append((cx - eps) / (-a_left + 1e-18))

        # Right wall: 1 - (cx + t * a_right) >= eps -> t * a_right <= (1 - cx - eps)
        if a_right > 0.0:
            t_candidates.append((0.5 - eps) / (a_right + 1e-18))

        # Bottom wall: cy + t * a_bottom >= eps
        if a_bottom < 0.0:
            t_candidates.append((cy - eps) / (-a_bottom + 1e-18))

        # Top wall: 1 - (cy + t * a_top) >= eps -> t <= (0.5 - eps) / a_top
        if a_top > 0.0:
            t_candidates.append((0.5 - eps) / (a_top + 1e-18))

    t_max = min(t_candidates)
    if not np.isfinite(t_max) or t_max <= 1.0:
        return x.copy(), y.copy(), r.copy()

    # Use slightly conservative t
    t = max(1.0, t_max * 0.999)

    # Apply scaling
    x2 = cx + t * (x - cx)
    y2 = cy + t * (y - cy)
    r2 = t * r

    # Clip within box (should already be satisfied)
    np.clip(x2, 0.0, 1.0, out=x2)
    np.clip(y2, 0.0, 1.0, out=y2)

    return x2, y2, r2


# ---------------------------------
# Strict feasibility polish
# ---------------------------------
def strict_feasibility_polish(x: np.ndarray, y: np.ndarray, r: np.ndarray, eps: float):
    """
    Final polish to ensure strict feasibility:
    - Clip centers to [r+eps, 1-r-eps]
    - Fix any overlaps by reducing the larger radius by half the violation
    - Repeat a few passes
    - Apply a tiny global shrink on radii to cement strict positivity of slacks
    """
    # Clip centers to be inside walls
    x = np.minimum(np.maximum(x, r + eps), 1.0 - r - eps)
    y = np.minimum(np.maximum(y, r + eps), 1.0 - r - eps)

    pts = np.column_stack([x, y])
    n = len(r)

    for _ in range(50):
        fixed = 0
        for i in range(n):
            for j in range(i + 1, n):
                dij = np.linalg.norm(pts[i] - pts[j])
                slack = dij - (r[i] + r[j]) - eps
                if slack < 0.0:
                    # Reduce larger radius by half the violation
                    excess = -slack
                    if r[i] >= r[j]:
                        r[i] = max(r[i] - 0.5 * excess, eps)
                    else:
                        r[j] = max(r[j] - 0.5 * excess, eps)
                    fixed += 1
        if fixed == 0:
            break

    # Final clip to walls
    x = np.minimum(np.maximum(x, r + eps), 1.0 - r - eps)
    y = np.minimum(np.maximum(y, r + eps), 1.0 - r - eps)

    # Tiny global shrink
    r = np.maximum(r * (1.0 - 1e-9), eps)

    return x, y, r


# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```
