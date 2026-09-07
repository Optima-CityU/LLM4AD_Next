Actionable mechanism that improved sum of radii under unit-square (perimeter ≤ 4) constraints using LP–mini-barrier–LP plus uniform inflation.

- TriHex Multi-start with LP–Mini-Barrier–LP Sandwich and Uniform Inflation: Use an LP–mini-barrier–LP sandwich after the main barrier+LP pipeline to nudge centers slightly, then convert the created slack into larger radii while accepting changes only if Σr increases; in this run it achieved sum_radii 2.3411713988448883 with validity 1.0, surpassing Parent 1 (2.336906346422657) and Parent 2 (2.329298847429417). Combine this with global uniform inflation about (0.5,0.5) until a box constraint becomes tight, followed by a fixed-center LP, which strictly increases Σr while preserving non-overlap with eps = 1e−6. Seed with a true hex-staggered multi-start (row patterns summing to 21 and ±1e−3 jitter) and select the best by Σr to feed stronger layouts into the sandwich. Reuse this pattern—hex-staggered multi-starts + interleaved fixed-center LP + uniform inflation + accept-only-if-Σr-increases—to reliably tighten contacts and exploit boundary slack under unit-square perimeter ≤ 4 constraints.
- HexTrust++ with Early Uniform Inflation and Adaptive LP Cadence: After the trust‑region stage, perform a single acceptance‑gated uniform inflation centered at (0.5, 0.5) that keeps all wall slacks strictly positive under eps0 and immediately follow it with a fixed‑center LP capture; combine this with an adaptive LP cadence that tightens the call interval K when near‑active wall/pair counts spike or block progress stalls and relaxes K when progress is smooth. This mechanism harvested boundary slack early while preserving non‑overlap, then promptly converted emerging slack into radius gains with monotone Σr acceptance (strict increase > 1e−14), and building the neighbor graph only after early inflation ensured the barrier stages reflected the scaled configuration. Evidence: the run achieved sum_radii 2.353971796321766 with validity 1.0, exceeding Parent 1 (2.3494688586580548) and Parent 2 (2.3462791824351803); future designs should reuse early, acceptance‑gated uniform inflation plus adaptive LP triggers to monetize slack and maintain monotone progress.

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
    # Reduce internal separation epsilon to release slack while preserving strict inequalities
    eps = 1e-6
    tiny = 1e-12

    # Utility: build all unordered pairs i<j for pairwise constraints
    pair_i = []
    pair_j = []
    for i in range(N):
        for j in range(i + 1, N):
            pair_i.append(i)
            pair_j.append(j)
    pair_i = np.array(pair_i, dtype=int)
    pair_j = np.array(pair_j, dtype=int)

    # Helper: exact LP inflation of radii for fixed centers (optional acceleration).
    # This removes residual slack for the current centers by solving:
    #   maximize sum r_i
    #   subject to 0 <= r_i <= m_i (m_i = min(x_i, 1-x_i, y_i, 1-y_i))
    #              r_i + r_j <= d_ij - eps
    # Uses scipy.optimize.linprog(method='highs') if available; otherwise no-op.
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

        # Build pairwise constraints matrix A_ub r <= b_ub for r_i + r_j <= d_ij - eps
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
                rhs = max(dij - eps_lp, 0.0)  # ensure feasibility even if centers too close
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
        # Clip numerical noise
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
    # - tiny jitter in [-1e-3,1e-3] to break symmetry, with clipping to interior
    def hex_seed(pattern: List[int], s: float, jitter: float = 1e-3) -> np.ndarray:
        v = s * (math.sqrt(3.0) / 2.0)
        centers: List[Tuple[float, float]] = []
        for k, n_k in enumerate(pattern):
            yk = 0.5 + (k - 2) * v
            row_shift = ((k % 2) - 0.5) * 0.5 * s
            for m in range(n_k):
                xk = 0.5 + row_shift + (m - (n_k - 1) / 2.0) * s
                centers.append((xk, yk))
        centers_arr = np.array(centers, dtype=float)
        # Add tiny jitter
        if jitter > 0:
            rng = np.random.default_rng()
            centers_arr += rng.uniform(-jitter, jitter, size=centers_arr.shape)
        # Clip into the unit square interior (leaving a tiny margin)
        centers_arr = np.clip(centers_arr, 1e-3, 1 - 1e-3)
        return centers_arr

    # Helper to run the barrier ascent + periodic LP inflation from an initial center seed.
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
            # Avoid division issues: ensure d >= tiny
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

        def objective(mu, slacks) -> float:
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
            return float(np.sum(r)) + mu * float(logs)

        # Ensure initial feasibility and tiny inflation if margins too tight
        slacks = compute_slacks(x, y, r)
        if not feasible(slacks):
            r = np.full(N, 0.005, dtype=float)
            slacks = compute_slacks(x, y, r)
            if not feasible(slacks):
                x = 0.5 + 0.8 * (x - 0.5)
                y = 0.5 + 0.8 * (y - 0.5)
                r = np.full(N, 0.003, dtype=float)
                slacks = compute_slacks(x, y, r)

        # Perform barrier ascent with periodic LP inflation after each mu stage
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
                f_curr = objective(mu, slacks)
                improved = False
                for _bt in range(40):
                    xn = x + step * grad_x
                    yn = y + step * grad_y
                    rn = r + step * grad_r
                    # Compute new slacks
                    slacks_n = compute_slacks(xn, yn, rn)
                    if feasible(slacks_n):
                        f_new = (np.sum(rn) + mu * (
                            np.sum(np.log(slacks_n[3]))
                            + np.sum(np.log(slacks_n[4]))
                            + np.sum(np.log(slacks_n[5]))
                            + np.sum(np.log(slacks_n[6]))
                            + np.sum(np.log(slacks_n[7]))
                            + np.sum(np.log(slacks_n[8]))
                        ))
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

            # Periodic fixed-center LP inflation after barrier stage
            centers_now = np.column_stack((x, y))
            r_lp = lp_inflate(centers_now, eps)
            if r_lp is not None and np.sum(r_lp) > np.sum(r) + tiny:
                r = r_lp
                # Recompute slacks with updated radii
                slacks = compute_slacks(x, y, r)
                # Ensure feasibility (LP should enforce, but be defensive)
                if not feasible(slacks):
                    # Slight shrink to restore strict feasibility
                    r *= 0.999
                    slacks = compute_slacks(x, y, r)

        # Final fixed-center LP inflation before uniform scaling
        centers_now = np.column_stack((x, y))
        r_lp = lp_inflate(centers_now, eps)
        if r_lp is not None and np.sum(r_lp) > np.sum(r) + tiny:
            r = r_lp

        # Final global uniform inflation about square center (0.5, 0.5)
        def global_uniform_inflation(xv, yv, rv, eps_g):
            # Constraints for each circle i:
            # s_xL' = 0.5 - eps + t*(x-0.5 - r) >= 0
            # s_xR' = 0.5 - eps - t*(x-0.5 + r) >= 0
            # s_yB' = 0.5 - eps + t*(y-0.5 - r) >= 0
            # s_yT' = 0.5 - eps - t*(y-0.5 + r) >= 0
            A = 0.5 - eps_g
            # Compute all B coefficients
            B_xL = xv - 0.5 - rv
            B_xR = -(xv - 0.5 + rv)
            B_yB = yv - 0.5 - rv
            B_yT = -(yv - 0.5 + rv)

            t_ubs = []

            def append_bounds(B_arr):
                # For each entry with B < 0, the upper bound is -A/B
                mask = B_arr < 0
                if np.any(mask):
                    t_ubs.extend(list((-A / B_arr[mask])))

            append_bounds(B_xL)
            append_bounds(B_xR)
            append_bounds(B_yB)
            append_bounds(B_yT)

            if len(t_ubs) == 0:
                # No upper bounds -> already at boundary or centered; no scaling change
                return xv, yv, rv

            t_max = min(t_ubs)
            # We aim to expand; only apply if t_max > 1
            if t_max > 1.0 + 1e-12:
                t = t_max * (1.0 - 1e-12)
                xv2 = 0.5 + t * (xv - 0.5)
                yv2 = 0.5 + t * (yv - 0.5)
                rv2 = t * rv
                return xv2, yv2, rv2
            else:
                return xv, yv, rv

        x, y, r = global_uniform_inflation(x, y, r, eps)

        # One more LP inflation after uniform scaling
        centers_now = np.column_stack((x, y))
        r_lp = lp_inflate(centers_now, eps)
        if r_lp is not None and np.sum(r_lp) > np.sum(r) + tiny:
            r = r_lp

        # Final polishing: small projection to ensure strict feasibility
        r = np.maximum(r, 1e-6)
        x = np.clip(x, r + eps, 1.0 - r - eps)
        y = np.clip(y, r + eps, 1.0 - r - eps)

        # Ensure pairwise separations satisfy d >= ri + rj + eps by small shrinking if needed
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

    # Multi-start hex seeding:
    # Patterns with a single 5-count row in different positions plus an additional
    # [5, 4, 5, 4, 3] pattern to expand plausible high-quality contact graphs.
    patterns = [
        [5, 4, 4, 4, 4],
        [4, 5, 4, 4, 4],
        [4, 4, 5, 4, 4],
        [4, 4, 4, 5, 4],
        [5, 4, 5, 4, 3],  # added pattern
    ]
    # Lattice spacings around 0.2 with slight variation
    spacings = [0.2 * 0.98, 0.2, 0.2 * 1.02]

    best_circles = None
    best_sum = -1.0

    rng_global = np.random.default_rng()

    for pat in patterns:
        for s in spacings:
            # Base seed with true hex staggering
            centers_seed = hex_seed(pat, s, jitter=1e-3)

            # Tiny multi-restarts per seed: create two additional jittered copies
            # using the same jitter magnitude (±1e−3), with clipping safeguards.
            seeds = [centers_seed]
            for _ in range(2):
                jitter = rng_global.uniform(-1e-3, 1e-3, size=centers_seed.shape)
                centers_j = np.clip(centers_seed + jitter, 1e-3, 1 - 1e-3)
                seeds.append(centers_j)

            # Run the pipeline for each seed and keep the best by sum of radii
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
    # This is a short local refinement that often squeezes extra Σr.
    def lp_mini_barrier_lp_refine(circles_in: np.ndarray) -> np.ndarray:
        x = circles_in[:, 0].copy()
        y = circles_in[:, 1].copy()
        r = circles_in[:, 2].copy()

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

        # First LP with frozen centers
        centers_now = np.column_stack((x, y))
        r_lp = lp_inflate(centers_now, eps)
        if r_lp is not None and np.sum(r_lp) > np.sum(r) + tiny:
            r = r_lp
        # Slight shrink to ensure strict feasibility for barrier (avoid log(0))
        r *= 0.999999
        slacks = compute_slacks(x, y, r)
        if not feasible(slacks):
            # If still not strictly feasible, shrink a touch more
            r *= 0.999
            slacks = compute_slacks(x, y, r)

        base_sum = float(np.sum(r))

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

        # Freeze centers again and re-run LP
        centers_now = np.column_stack((x, y))
        r_lp2 = lp_inflate(centers_now, eps)
        if r_lp2 is not None and np.sum(r_lp2) > np.sum(r) + tiny:
            r_new = r_lp2
        else:
            r_new = r

        # Accept only if Σr increased
        if np.sum(r_new) > base_sum + tiny:
            x_acc, y_acc, r_acc = x, y, r_new
        else:
            # No improvement, return original
            return circles_in

        # Optional second global uniform inflation about (0.5,0.5), then LP.
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

        x2, y2, r2 = global_uniform_inflation(x_acc, y_acc, r_acc, eps)
        centers_now2 = np.column_stack((x2, y2))
        r_lp3 = lp_inflate(centers_now2, eps)
        if r_lp3 is not None and np.sum(r_lp3) > np.sum(r2) + tiny:
            r2 = r_lp3

        if np.sum(r2) > np.sum(r_acc) + tiny:
            x_acc, y_acc, r_acc = x2, y2, r2

        # Final safety projection and overlap polish (strict feasibility)
        r_acc = np.maximum(r_acc, 1e-6)
        x_acc = np.clip(x_acc, r_acc + eps, 1.0 - r_acc - eps)
        y_acc = np.clip(y_acc, r_acc + eps, 1.0 - r_acc - eps)
        for _ in range(3):
            dxp = x_acc[pair_i] - x_acc[pair_j]
            dyp = y_acc[pair_i] - y_acc[pair_j]
            d = np.hypot(dxp, dyp)
            need = r_acc[pair_i] + r_acc[pair_j] + eps
            viol = need - d
            if np.all(viol <= 0):
                break
            for k in range(len(pair_i)):
                if viol[k] > 0:
                    i = pair_i[k]; j = pair_j[k]
                    if r_acc[i] >= r_acc[j]:
                        r_acc[i] = max(r_acc[i] - 0.5 * viol[k], 1e-6)
                    else:
                        r_acc[j] = max(r_acc[j] - 0.5 * viol[k], 1e-6)
            x_acc = np.clip(x_acc, r_acc + eps, 1.0 - r_acc - eps)
            y_acc = np.clip(y_acc, r_acc + eps, 1.0 - r_acc - eps)

        return np.column_stack((x_acc, y_acc, r_acc))

    # Apply refinement and accept only if Σr increases (guard inside function already).
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
"""High-performance candidate for packing 21 circles in a perimeter-four rectangle.

Implements the algorithm described in the prompt:

- Packs inside the unit square (W + H = 2), ensuring the rectangle perimeter ≤ 4.
- Multi-start hexagonal seeding with several 5-row patterns that sum to 21, plus anisotropic "aspect-crawl" variants.
- Fixed-center LP allocation (exact via SciPy HiGHS when available) for radii, with safe fallback.
- Trust-region growth-and-move stage with periodic LP reallocations and an adaptive cadence.
- Early uniform inflation warm-start immediately after trust-region + initial LP (acceptance-gated), followed by LP capture.
- Barrier ascent using a smooth interior log-barrier on slacks with lazy augmentation of pairwise constraints.
- Periodic LP inflations to reallocate slack created by center moves.
- Mid and near-end uniform inflations about the box center to convert boundary slack into larger radii.
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

        # Early uniform inflation warm-start (Parent 2 addition), followed by immediate LP capture
        x2, y2, r2 = uniform_inflation(x, y, r, eps0)
        if np.sum(r2) > np.sum(r) + 1e-14:
            x, y, r = x2, y2, r2
            r_new = fixed_center_inflate(np.column_stack([x, y]), r, eps0)
            if np.sum(r_new) > np.sum(r) + 1e-14:
                r = r_new

        # Initial sparse neighbor graph (k-NN) after early uniform inflation
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
