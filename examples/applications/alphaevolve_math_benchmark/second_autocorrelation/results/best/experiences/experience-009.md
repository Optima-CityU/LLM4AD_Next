Insight from the BACLIP-AB run on the second autocorrelation inequality task, focusing on block-level LP moves and alternation bisection that directly target the central Linf plateau.

- Block-Active Chebyshev LP with Alternation Bisection (BACLIP-AB): It replaces per-index tweaks with isotonic block variables and solves a tiny block-active LP on central Linf-active nodes under constraints g_active + J_block Δ ≤ t, mass preservation ∑|B_b|Δ_b = 0, and trust-region bounds |Δ_b| ≤ ε_b, enabling coherent plateau-level moves that flatten the central Linf peak without breaking monotonicity.
- Alternation bisection (AB): It transfers mass from the next block B1 to the central block B0 via a bracketed 1D bisection within isotonic and nonnegativity bounds, stopping when the top-K central convolution nodes are near-equal or when the exact ratio R stops improving, explicitly enforcing a Chebyshev-like alternation on the peak.
- Block-Active Chebyshev LP with Alternation Bisection (BACLIP-AB): In Generation 4, it achieved target_ratio 0.8073832880432185 with c_lower_bound 0.7236576410731367 and validity 1.0, indicating stable effectiveness of the block LP and alternation polish under the existing pipeline and acceptance rules.

```python
#!/usr/bin/env python3
"""Structure-aware optimizer for the second autocorrelation inequality.

This implements a deterministic, structure-aware optimizer that
constructs a nonnegative, symmetric, monotone step function f
on 50 equally spaced intervals over [-1/4, 1/4], tailored to
maximize the ratio

    R = ||f * f||_2^2 / ( ||f * f||_1 * ||f * f|_∞ )

under the convolution structure of a piecewise-constant f.

Key features:
- Symmetric, unimodal (monotone from the center outward) step function.
- Exact evaluation for the L2 term of the convolution via piecewise linear integration.
- Monotone Pairwise Balancing (MPB) with exact bracketing line-search moves.
- Dual-Scale Mass Transfers (DSMT) over (j, j+2) and (j, j+3) with exact line searches.
- New: Block-aware Jacobian-Guided Central Plateau Equalization (block-JG-CPE),
  replacing per-index tweaks with an active-set linearized step over isotonic blocks.
- New: Alternation Bisection polish (AB) that enforces near-Chebyshev alternation by a
  1D mass transfer between the central blocks.
- Tail Siphon Transfers (TST) to siphon small mass from far tails to the near-center.

The function optimize_lower_bound returns a heights list of length 50 and the
corresponding lower bound estimate as a float. The heights satisfy the expected
constraints and are designed to pass verification checks.

Notes:
- We do not modify external cal_lower_bound or verify_heights_sequence; the
  internal objective here mirrors the baseline structure (piecewise linear model).
- The new block-aware central equalization is localized: it replaces the previous
  index-level JG-CPE micro-step and adds a short alternation bisection polish.
"""

import json
import math
from typing import List, Tuple

import numpy as np


# EVOLVE_START
def optimize_lower_bound() -> Tuple[List[float], float]:
    """
    Optimize a symmetric, monotone, nonnegative step function (50 bins)
    to maximize the autocorrelation ratio R. Returns:
    - heights: list of 50 floats (nonnegative, symmetric, monotone), normalized sum=50
    - c_lower_bound: float ratio computed with the same formula structure as the baseline
    """

    n = 50  # number of bins
    half_len = n // 2  # 25
    assert half_len * 2 == n

    # Core objective computation consistent with baseline logic:
    # - Use discrete convolution of heights with itself.
    # - Treat g(t) as piecewise linear between uniform grid nodes with zero endpoints.
    # - L2^2 via exact integral for linear segments: sum w/3*(v_i^2 + v_i v_{i+1} + v_{i+1}^2)
    # - L1 via trapezoid reduces to width * sum of internal node values because endpoints are zero.
    # - Linf via max node value.
    def compute_ratio(heights: np.ndarray) -> float:
        conv = np.convolve(heights, heights)  # node values on inner grid (length 2n-1)
        widths = np.diff(np.linspace(-0.5, 0.5, len(conv) + 2))  # uniform segment widths
        values = np.concatenate(([0.0], conv.astype(float), [0.0]))
        # Exact L2^2 for piecewise linear segments
        l2_sq = 0.0
        for i in range(len(conv) + 1):
            vi = values[i]
            vj = values[i + 1]
            w = widths[i]
            l2_sq += w / 3.0 * (vi * vi + vi * vj + vj * vj)
        # L1 equals width * sum(nodes) because endpoints are zero; keep in baseline-equivalent form
        l1 = float(np.sum(np.abs(conv)) / (len(conv) + 1))
        linf = float(np.max(np.abs(conv)))
        if l1 <= 0.0 or linf <= 0.0:
            return 0.0
        return float(l2_sq / (l1 * linf))

    # Helper: convert half-sequence (length 25, center-out monotone nonincreasing)
    # into full symmetric heights (length 50).
    def build_full_from_half(a_half: np.ndarray) -> np.ndarray:
        # a_half[0] is the center level; a_half[k] monotonically nonincreasing in k
        # Full sequence: indices 0..49. The "center" lies between 24 and 25.
        # We map a_half to full: h[24] = h[25] = a_half[0], then outward mirrored.
        left = a_half[1:][::-1]  # 24 values for indices 0..23
        center_two = np.array([a_half[0], a_half[0]])  # indices 24 and 25
        right = a_half[1:]  # 24 values for indices 26..49
        return np.concatenate([left, center_two, right])

    # Helper: normalize heights to have sum = n (scale-invariant but keeps consistency)
    def normalize_to_sum_n(h: np.ndarray) -> np.ndarray:
        s = np.sum(h)
        if s <= 0:
            return np.ones_like(h)
        return h * (n / s)

    # Projection: enforce monotone nonincreasing for the half-sequence via PAV
    # and nonnegativity. a_half length is 25.
    def project_monotone_nonincreasing(a_half: np.ndarray) -> np.ndarray:
        # We'll project onto the cone of nonincreasing sequences with a simple PAV.
        a = a_half.copy()
        a[a < 0] = 0.0
        # PAV for nonincreasing: ensure a[i] >= a[i+1]
        starts = list(range(len(a)))
        ends = list(range(len(a)))
        vals = a.tolist()
        k = 0
        while k < len(vals) - 1:
            if vals[k] < vals[k + 1]:  # violation of nonincreasing
                len_k = ends[k] - starts[k] + 1
                len_k1 = ends[k + 1] - starts[k + 1] + 1
                new_val = (vals[k] * len_k + vals[k + 1] * len_k1) / (len_k + len_k1)
                vals[k] = new_val
                ends[k] = ends[k + 1]
                del vals[k + 1]
                del starts[k + 1]
                del ends[k + 1]
                if k > 0:
                    k -= 1
            else:
                k += 1
        a_proj = np.zeros_like(a)
        for b in range(len(vals)):
            a_proj[starts[b] : ends[b] + 1] = vals[b]
        a_proj[a_proj < 0] = 0.0
        return a_proj

    # Construct initial seeds: plateau + geometric taper templates, plus a few simple shapes.
    def make_seed_half(P: int, r: float) -> np.ndarray:
        a = np.zeros(half_len, dtype=float)
        for k in range(half_len):
            if k < P:
                a[k] = 1.0
            else:
                a[k] = (r ** (k - P + 1))
        return a

    def make_linear_taper_half(width: int) -> np.ndarray:
        a = np.zeros(half_len, dtype=float)
        for k in range(half_len):
            val = max(0.0, 1.0 - k / max(1, width))
            a[k] = val
        return a

    # Generate initial seeds
    seeds = []
    # Plateau widths P from 1 to 16; ratios r in [0.68, 0.98]
    r_grid = [0.68, 0.72, 0.75, 0.78, 0.80, 0.83, 0.86, 0.88, 0.90, 0.92, 0.94, 0.96, 0.98]
    for P in range(1, 17):
        for r in r_grid:
            seeds.append(make_seed_half(P, r))
    # Add linear tapers
    for W in [4, 6, 8, 10, 12, 16, 20, 24]:
        seeds.append(make_linear_taper_half(W))
    # Add a "flat" seed (constant half), equivalent to all ones heights
    seeds.append(np.ones(half_len, dtype=float))

    # Normalize seeds to sum n on the full sequence
    def seed_to_full(hhalf: np.ndarray) -> np.ndarray:
        hhalf = project_monotone_nonincreasing(hhalf)
        full = build_full_from_half(hhalf)
        return normalize_to_sum_n(full)

    # Evaluate seeds and keep the top K
    seed_pairs = []
    for a in seeds:
        h = seed_to_full(a)
        seed_pairs.append((h, compute_ratio(h)))
    seed_pairs.sort(key=lambda t: t[1], reverse=True)
    topK = min(28, len(seed_pairs))
    seed_pairs = seed_pairs[:topK]

    # Line search along adjacent pairwise move direction (MPB) for half-sequence indices j and j+1
    def line_search_pair(
        a_half: np.ndarray, j: int, ratio_current: float, max_iter: int = 18
    ) -> Tuple[float, float]:
        # Compute feasible interval [LB, UB] for delta on pair (j, j+1)
        LB = -float("inf")
        UB = float("inf")
        # Nonnegativity
        LB = max(LB, -a_half[j])
        UB = min(UB, a_half[j + 1])
        # Maintain neighbors
        if j > 0:
            UB = min(UB, a_half[j - 1] - a_half[j])
        if j + 2 < len(a_half):
            UB = min(UB, a_half[j + 1] - a_half[j + 2])
        # Pairwise monotone after move: a[j] + delta >= a[j+1] - delta => delta >= (a[j+1] - a[j])/2
        LB = max(LB, (a_half[j + 1] - a_half[j]) * 0.5)

        if not (LB < UB):
            return 0.0, ratio_current  # no move possible

        phi = (1 + math.sqrt(5)) / 2.0
        invphi = 1.0 / phi
        aL = LB
        aR = UB

        x1 = aR - (aR - aL) * invphi
        x2 = aL + (aR - aL) * invphi

        def eval_delta(d: float) -> float:
            b = a_half.copy()
            b[j] += d
            b[j + 1] -= d
            h = build_full_from_half(b)
            h = normalize_to_sum_n(h)
            return compute_ratio(h)

        r1 = eval_delta(x1)
        r2 = eval_delta(x2)

        best_r = ratio_current
        best_d = 0.0
        if r1 > best_r:
            best_r = r1
            best_d = x1
        if r2 > best_r:
            best_r = r2
            best_d = x2

        for _ in range(max_iter):
            if (aR - aL) <= 1e-12:
                break
            if r1 < r2:
                aL = x1
                x1 = x2
                r1 = r2
                x2 = aL + (aR - aL) * invphi
                r2 = eval_delta(x2)
                if r2 > best_r:
                    best_r = r2
                    best_d = x2
            else:
                aR = x2
                x2 = x1
                r2 = r1
                x1 = aR - (aR - aL) * invphi
                r1 = eval_delta(x1)
                if r1 > best_r:
                    best_r = r1
                    best_d = x1

        # Check endpoints
        rL = eval_delta(LB)
        if rL > best_r:
            best_r = rL
            best_d = LB
        rU = eval_delta(UB)
        if rU > best_r:
            best_r = rU
            best_d = UB

        return best_d, best_r

    # DSMT: line search for longer-range transfer from k to j (k=j+2 or j+3)
    def line_search_long_pair(
        a_half: np.ndarray, j: int, k: int, ratio_current: float, max_iter: int = 18
    ) -> Tuple[float, float]:
        # We move delta >= 0 from a[k] to a[j]: a[j] += d, a[k] -= d
        if not (0 <= j < half_len and 0 <= k < half_len and j < k):
            return 0.0, ratio_current
        UB = a_half[k]
        if j > 0:
            UB = min(UB, a_half[j - 1] - a_half[j])
        if k + 1 < half_len:
            UB = min(UB, a_half[k] - a_half[k + 1])
        LB = 0.0
        if not (LB < UB):
            return 0.0, ratio_current

        phi = (1 + math.sqrt(5)) / 2.0
        invphi = 1.0 / phi
        aL = LB
        aR = UB
        x1 = aR - (aR - aL) * invphi
        x2 = aL + (aR - aL) * invphi

        def eval_delta(d: float) -> float:
            b = a_half.copy()
            b[j] += d
            b[k] -= d
            h = build_full_from_half(b)
            h = normalize_to_sum_n(h)
            return compute_ratio(h)

        r1 = eval_delta(x1)
        r2 = eval_delta(x2)

        best_r = ratio_current
        best_d = 0.0
        if r1 > best_r:
            best_r = r1
            best_d = x1
        if r2 > best_r:
            best_r = r2
            best_d = x2

        for _ in range(max_iter):
            if (aR - aL) <= 1e-12:
                break
            if r1 < r2:
                aL = x1
                x1 = x2
                r1 = r2
                x2 = aL + (aR - aL) * invphi
                r2 = eval_delta(x2)
                if r2 > best_r:
                    best_r = r2
                    best_d = x2
            else:
                aR = x2
                x2 = x1
                r2 = r1
                x1 = aR - (aR - aL) * invphi
                r1 = eval_delta(x1)
                if r1 > best_r:
                    best_r = r1
                    best_d = x1

        # Check endpoints
        rL = eval_delta(LB)
        if rL > best_r:
            best_r = rL
            best_d = LB
        rU = eval_delta(UB)
        if rU > best_r:
            best_r = rU
            best_d = UB

        return best_d, best_r

    # Legacy CPE fallback: central micro transfer over m+1 levels
    def line_search_cpe(
        a_half: np.ndarray, m: int, ratio_current: float, max_iter: int = 22
    ) -> Tuple[float, float]:
        assert 1 <= m <= min(4, half_len - 1)
        a0 = a_half[0]
        a1 = a_half[1]
        ub_nonneg = a0 / float(m) if a0 > 0 else 0.0
        ub_monotone = (a0 - a1) / float(m + 1) if (a0 - a1) > 0 else 0.0
        UB = max(0.0, min(ub_nonneg, ub_monotone))
        LB = 0.0
        if not (LB < UB):
            return 0.0, ratio_current

        phi = (1 + math.sqrt(5)) / 2.0
        invphi = 1.0 / phi
        aL = LB
        aR = UB
        x1 = aR - (aR - aL) * invphi
        x2 = aL + (aR - aL) * invphi

        def eval_alpha(alpha: float) -> float:
            b = a_half.copy()
            b[0] -= m * alpha
            b[0] = max(0.0, b[0])
            for i in range(1, m + 1):
                b[i] += alpha
            b = project_monotone_nonincreasing(b)
            h = build_full_from_half(b)
            h = normalize_to_sum_n(h)
            return compute_ratio(h)

        r1 = eval_alpha(x1)
        r2 = eval_alpha(x2)

        best_r = ratio_current
        best_a = 0.0
        if r1 > best_r:
            best_r = r1
            best_a = x1
        if r2 > best_r:
            best_r = r2
            best_a = x2

        for _ in range(max_iter):
            if (aR - aL) <= 1e-12:
                break
            if r1 < r2:
                aL = x1
                x1 = x2
                r1 = r2
                x2 = aL + (aR - aL) * invphi
                r2 = eval_alpha(x2)
                if r2 > best_r:
                    best_r = r2
                    best_a = x2
            else:
                aR = x2
                x2 = x1
                r2 = r1
                x1 = aR - (aR - aL) * invphi
                r1 = eval_alpha(x1)
                if r1 > best_r:
                    best_r = r1
                    best_a = x1

        rL = eval_alpha(LB)
        if rL > best_r:
            best_r = rL
            best_a = LB
        rU = eval_alpha(UB)
        if rU > best_r:
            best_r = rU
            best_a = UB

        return best_a, best_r

    # Utilities for block-aware central equalization ---------------------------------------------

    # Identify isotonic blocks (constant plateaus) in a nonincreasing a_half.
    def get_isotonic_blocks(a_half: np.ndarray) -> List[Tuple[int, int, float]]:
        blocks = []
        L = len(a_half)
        i = 0
        while i < L:
            j = i
            v = a_half[i]
            # Merge while equal within tiny tolerance
            while j + 1 < L and abs(a_half[j + 1] - v) < 1e-12:
                j += 1
            blocks.append((i, j, v))
            i = j + 1
        return blocks

    # Build central active set of convolution nodes: those near Linf around center, with neighbors.
    def central_active_indices(h_full: np.ndarray, M_nodes: int = 7, tol: float = 1e-12) -> List[int]:
        conv = np.convolve(h_full, h_full)
        L = len(conv)
        mid = L // 2
        # Choose a contiguous window around the max near mid of width M_nodes
        start = max(0, mid - (M_nodes // 2))
        end = min(L - 1, start + M_nodes - 1)
        idxs = list(range(start, end + 1))
        # Optionally expand by neighbors if equal to max (guard)
        gmax = float(np.max(conv))
        for k in range(max(0, start - 1), min(L - 1, end + 1) + 1):
            if conv[k] >= gmax - tol and k not in idxs:
                idxs.append(k)
        idxs.sort()
        return idxs

    # Finite difference Jacobian of central convolution nodes with respect to block heights.
    # We perturb block b by +delta uniformly across its indices in a_half (before mapping to full),
    # build the full sequence, normalize, evaluate conv on active indices, and difference quotient.
    def finite_diff_J_blocks(
        a_half: np.ndarray,
        blocks: List[Tuple[int, int, float]],
        idxs_active: List[int],
        delta: float = 1e-3,
    ) -> Tuple[np.ndarray, np.ndarray]:
        # Base values
        h0 = build_full_from_half(a_half)
        h0 = normalize_to_sum_n(h0)
        conv0 = np.convolve(h0, h0)
        g0 = np.array([conv0[i] for i in idxs_active], dtype=float)

        B = len(blocks)
        M = len(idxs_active)
        J = np.zeros((M, B), dtype=float)

        for b, (s, e, _) in enumerate(blocks):
            # Perturb block b by +delta
            ah = a_half.copy()
            ah[s : e + 1] += delta
            # No projection here to keep directional meaning; tiny delta
            h = build_full_from_half(ah)
            h = normalize_to_sum_n(h)
            conv = np.convolve(h, h)
            gd = np.array([conv[i] for i in idxs_active], dtype=float)
            J[:, b] = (gd - g0) / delta

        return J, g0

    # Block-aware Jacobian-Guided Central Plateau Equalization (block-JG-CPE)
    # Solve a tiny KKT system to minimize ||J Δ + 1||^2 subject to mass preservation over blocks:
    # sum_b (len_b Δ_b) = 0. Then scale Δ by alpha to respect trust region and isotonic constraints.
    def block_lp_cpe_step(
        a_half: np.ndarray,
        ratio_current: float,
        max_blocks: int = 7,
        M_nodes: int = 7,
        eps_box: float = 0.04,
    ) -> Tuple[np.ndarray, float, bool]:
        # Parse blocks and restrict to the most central ones (starting from block 0 outward).
        blocks_all = get_isotonic_blocks(project_monotone_nonincreasing(a_half))
        if len(blocks_all) <= 1:
            return a_half, ratio_current, False

        blocks = blocks_all[: min(max_blocks, len(blocks_all))]
        # Build active set and Jacobian
        h_full = build_full_from_half(a_half)
        h_full = normalize_to_sum_n(h_full)
        idxs_active = central_active_indices(h_full, M_nodes=M_nodes)
        # If no meaningful active nodes, bail
        if len(idxs_active) == 0:
            return a_half, ratio_current, False

        try:
            J, g0 = finite_diff_J_blocks(a_half, blocks, idxs_active, delta=1e-3)
        except Exception:
            return a_half, ratio_current, False

        B = len(blocks)
        # Right-hand side: encourage uniform reduction across active nodes
        ones_vec = np.ones((len(idxs_active),), dtype=float)
        # Mass constraint: sum(len_b Δ_b) = 0
        len_vec = np.array([blocks[b][1] - blocks[b][0] + 1 for b in range(B)], dtype=float)

        # Solve KKT: minimize ||JΔ + 1||^2 s.t. LΔ=0
        # KKT:
        # [J^T J, L^T; L, 0] [Δ; λ] = [-J^T 1; 0]
        JTJ = J.T @ J
        rhs = -J.T @ ones_vec
        KKT = np.zeros((B + 1, B + 1), dtype=float)
        KKT[:B, :B] = JTJ
        KKT[:B, B] = len_vec
        KKT[B, :B] = len_vec
        rhs_full = np.zeros((B + 1,), dtype=float)
        rhs_full[:B] = rhs
        # Solve with tiny ridge regularization if needed
        solved = False
        for ridge in (0.0, 1e-10, 1e-8, 1e-6):
            K = KKT.copy()
            if ridge > 0.0:
                K[:B, :B] += ridge * np.eye(B)
            try:
                sol = np.linalg.solve(K, rhs_full)
                delta_blocks = sol[:B]
                solved = True
                break
            except np.linalg.LinAlgError:
                continue
        if not solved:
            return a_half, ratio_current, False

        # If direction is tiny, abort
        if np.linalg.norm(delta_blocks, ord=2) < 1e-12:
            return a_half, ratio_current, False

        # Compute trust-region and isotonic feasibility scaling alpha_max
        # Gather block heights for constraints
        block_vals = np.array([blocks[b][2] for b in range(B)], dtype=float)

        alpha_cands = []

        # Box trust: |α Δ_b| ≤ eps_box
        for b in range(B):
            if abs(delta_blocks[b]) > 1e-15:
                alpha_cands.append(eps_box / abs(delta_blocks[b]))

        # Nonnegativity: block value + αΔ >= 0
        for b in range(B):
            if delta_blocks[b] < 0.0:
                vb = block_vals[b]
                if vb <= 0.0:
                    return a_half, ratio_current, False
                alpha_cands.append(vb / (-delta_blocks[b]))

        # Monotonicity across included blocks: ensure a_b-1 + αΔ_{b-1} >= a_b + αΔ_b
        for b in range(1, B):
            gap = block_vals[b - 1] - block_vals[b]  # >= 0
            dd = delta_blocks[b - 1] - delta_blocks[b]
            if dd < 0.0:
                # α ≤ gap / -dd
                if -dd > 1e-15:
                    alpha_cands.append(gap / (-dd))

        # Boundary with next block beyond last included (Δ = 0 for outside region)
        if B < len(blocks_all):
            v_last = block_vals[B - 1]
            v_next = blocks_all[B][2]  # height of the next block
            gap = v_last - v_next
            if delta_blocks[B - 1] < 0.0:
                alpha_cands.append(gap / (-delta_blocks[B - 1]) if -delta_blocks[B - 1] > 1e-15 else 0.0)

        if len(alpha_cands) == 0:
            return a_half, ratio_current, False

        alpha_max = max(0.0, min(alpha_cands))
        if not (alpha_max > 1e-12):
            return a_half, ratio_current, False

        # Try scaled alphas and pick best improvement
        scales = [0.95, 0.75, 0.5, 0.35]
        best_ratio = ratio_current
        best_half = a_half.copy()
        improved = False

        # Helper: apply Δ on blocks with scaling α, project, renormalize, and score
        def apply_block_step(alpha: float) -> Tuple[np.ndarray, float]:
            bnew = a_half.copy()
            for idx_b, (s, e, _) in enumerate(blocks):
                bnew[s : e + 1] += alpha * delta_blocks[idx_b]
            # Project and normalize
            bnew = project_monotone_nonincreasing(bnew)
            h = build_full_from_half(bnew)
            h = normalize_to_sum_n(h)
            r = compute_ratio(h)
            return bnew, r

        for sc in scales:
            alpha = sc * alpha_max
            if alpha <= 1e-12:
                continue
            bnew, r = apply_block_step(alpha)
            if r > best_ratio + 1e-13:
                best_ratio = r
                best_half = bnew
                improved = True

        return best_half, best_ratio, improved

    # Alternation bisection polish: move mass between central block B0 and next block B1
    # We perform a 1D search over β ∈ [0, β_max] that transfers mass from B1 to B0 and evaluate
    # the exact ratio, stopping when no further improvement is obtained.
    def alternation_bisection_polish(
        a_half: np.ndarray,
        ratio_current: float,
        max_iter: int = 22,
        trust: float = 0.18,
    ) -> Tuple[np.ndarray, float, bool]:
        blocks = get_isotonic_blocks(project_monotone_nonincreasing(a_half))
        if len(blocks) < 2:
            return a_half, ratio_current, False
        # Central blocks
        s0, e0, v0 = blocks[0]
        s1, e1, v1 = blocks[1]
        # Feasible β range: keep a1 >= a2, nonnegativity for block1
        v2 = blocks[2][2] if len(blocks) >= 3 else 0.0
        beta_max = min(v1, v1 - v2 + 1e-12)  # ensure block1 remains >= block2
        beta_max = max(0.0, beta_max)
        beta_max = min(beta_max, trust)
        if beta_max <= 1e-15:
            return a_half, ratio_current, False

        # Golden-section search on β to maximize ratio
        phi = (1 + math.sqrt(5)) / 2.0
        invphi = 1.0 / phi
        aL = 0.0
        aR = beta_max
        x1 = aR - (aR - aL) * invphi
        x2 = aL + (aR - aL) * invphi

        def eval_beta(beta: float) -> float:
            b = a_half.copy()
            b[s0 : e0 + 1] += beta
            b[s1 : e1 + 1] -= beta
            b = project_monotone_nonincreasing(b)
            h = build_full_from_half(b)
            h = normalize_to_sum_n(h)
            return compute_ratio(h)

        r1 = eval_beta(x1)
        r2 = eval_beta(x2)

        best_r = ratio_current
        best_b = 0.0
        if r1 > best_r:
            best_r = r1
            best_b = x1
        if r2 > best_r:
            best_r = r2
            best_b = x2

        for _ in range(max_iter):
            if (aR - aL) <= 1e-12:
                break
            if r1 < r2:
                aL = x1
                x1 = x2
                r1 = r2
                x2 = aL + (aR - aL) * invphi
                r2 = eval_beta(x2)
                if r2 > best_r:
                    best_r = r2
                    best_b = x2
            else:
                aR = x2
                x2 = x1
                r2 = r1
                x1 = aR - (aR - aL) * invphi
                r1 = eval_beta(x1)
                if r1 > best_r:
                    best_r = r1
                    best_b = x1

        # Endpoints
        rL = eval_beta(0.0)
        if rL > best_r:
            best_r = rL
            best_b = 0.0
        rU = eval_beta(beta_max)
        if rU > best_r:
            best_r = rU
            best_b = beta_max

        if best_r > ratio_current + 1e-12 and best_b != 0.0:
            b = a_half.copy()
            b[s0 : e0 + 1] += best_b
            b[s1 : e1 + 1] -= best_b
            b = project_monotone_nonincreasing(b)
            return b, best_r, True

        return a_half, ratio_current, False

    # Tail siphon transfers (TST): siphon small mass from far tails (k≥8) to near center (j in {1,2,3})
    def tst_sweep(
        a_half: np.ndarray, ratio_current: float, trust: float = 0.08
    ) -> Tuple[np.ndarray, float, bool]:
        improved = False
        best_ratio = ratio_current
        best_half = a_half
        # Candidate pairs
        j_candidates = [1, 2, 3]
        k_candidates = [8, 10, 12, 14, 16, 18, 20, 22, 24]
        # For each pair, do a small golden-section line search with UB capped by trust region
        for j in j_candidates:
            for k in k_candidates:
                if not (j < k and k < half_len):
                    continue
                # Compute feasible UB from constraints
                UB = a_half[k]
                if j > 0:
                    UB = min(UB, a_half[j - 1] - a_half[j])
                if k + 1 < half_len:
                    UB = min(UB, a_half[k] - a_half[k + 1])
                if UB <= 1e-15:
                    continue
                UB = min(UB, trust)
                if UB <= 1e-15:
                    continue

                phi = (1 + math.sqrt(5)) / 2.0
                invphi = 1.0 / phi
                aL = 0.0
                aR = UB
                x1 = aR - (aR - aL) * invphi
                x2 = aL + (aR - aL) * invphi

                def eval_delta(d: float) -> float:
                    b = a_half.copy()
                    b[j] += d
                    b[k] -= d
                    b = project_monotone_nonincreasing(b)
                    h = build_full_from_half(b)
                    h = normalize_to_sum_n(h)
                    return compute_ratio(h)

                r1 = eval_delta(x1)
                r2 = eval_delta(x2)

                local_best_r = ratio_current
                local_best_d = 0.0
                if r1 > local_best_r:
                    local_best_r = r1
                    local_best_d = x1
                if r2 > local_best_r:
                    local_best_r = r2
                    local_best_d = x2

                for _ in range(16):
                    if (aR - aL) <= 1e-12:
                        break
                    if r1 < r2:
                        aL = x1
                        x1 = x2
                        r1 = r2
                        x2 = aL + (aR - aL) * invphi
                        r2 = eval_delta(x2)
                        if r2 > local_best_r:
                            local_best_r = r2
                            local_best_d = x2
                    else:
                        aR = x2
                        x2 = x1
                        r2 = r1
                        x1 = aR - (aR - aL) * invphi
                        r1 = eval_delta(x1)
                        if r1 > local_best_r:
                            local_best_r = r1
                            local_best_d = x1

                # Check endpoints
                rL = eval_delta(0.0)
                if rL > local_best_r:
                    local_best_r = rL
                    local_best_d = 0.0
                rU = eval_delta(UB)
                if rU > local_best_r:
                    local_best_r = rU
                    local_best_d = UB

                if local_best_r > best_ratio + 1e-12 and local_best_d > 0.0:
                    # Apply
                    b = a_half.copy()
                    b[j] += local_best_d
                    b[k] -= local_best_d
                    b = project_monotone_nonincreasing(b)
                    best_half = b
                    best_ratio = local_best_r
                    improved = True

        return best_half, best_ratio, improved

    # Monotone Pairwise Balancing + DSMT + Block-JG-CPE + AB + TST optimization for a given starting half-sequence
    def optimize_half(a_half_init: np.ndarray, max_cycles: int = 90) -> Tuple[np.ndarray, float]:
        a_half = project_monotone_nonincreasing(a_half_init)
        h_full = build_full_from_half(a_half)
        h_full = normalize_to_sum_n(h_full)
        best_ratio = compute_ratio(h_full)

        for _ in range(max_cycles):
            improved_cycle = False

            # MPB sweep: adjacent pairs from center outward
            for j in range(0, half_len - 1):
                d_star, r_star = line_search_pair(a_half, j, best_ratio)
                if r_star > best_ratio + 1e-12:
                    a_half[j] += d_star
                    a_half[j + 1] -= d_star
                    a_half = project_monotone_nonincreasing(a_half)
                    h_full = build_full_from_half(a_half)
                    h_full = normalize_to_sum_n(h_full)
                    best_ratio = compute_ratio(h_full)
                    improved_cycle = True

            # DSMT sweep 1: pairs (j, j+2)
            for j in range(0, half_len - 2):
                d_star, r_star = line_search_long_pair(a_half, j, j + 2, best_ratio)
                if r_star > best_ratio + 1e-12:
                    a_half[j] += d_star
                    a_half[j + 2] -= d_star
                    a_half = project_monotone_nonincreasing(a_half)
                    h_full = build_full_from_half(a_half)
                    h_full = normalize_to_sum_n(h_full)
                    best_ratio = compute_ratio(h_full)
                    improved_cycle = True

            # DSMT sweep 2: pairs (j, j+3)
            for j in range(0, half_len - 3):
                d_star, r_star = line_search_long_pair(a_half, j, j + 3, best_ratio)
                if r_star > best_ratio + 1e-12:
                    a_half[j] += d_star
                    a_half[j + 3] -= d_star
                    a_half = project_monotone_nonincreasing(a_half)
                    h_full = build_full_from_half(a_half)
                    h_full = normalize_to_sum_n(h_full)
                    best_ratio = compute_ratio(h_full)
                    improved_cycle = True

            # Project and normalize after sweeps
            a_half = project_monotone_nonincreasing(a_half)
            h_full = build_full_from_half(a_half)
            h_full = normalize_to_sum_n(h_full)
            cur_ratio = compute_ratio(h_full)
            if cur_ratio > best_ratio + 1e-14:
                best_ratio = cur_ratio
                improved_cycle = True

            # Block-JG-CPE: sensitivity-aware central equalization on isotonic blocks
            # Try two epsilons
            for eps_try in (0.03, 0.05):
                a_half_candidate, r_candidate, imp = block_lp_cpe_step(
                    a_half, best_ratio, max_blocks=7, M_nodes=7, eps_box=eps_try
                )
                if imp and r_candidate > best_ratio + 1e-12:
                    a_half = project_monotone_nonincreasing(a_half_candidate)
                    h_full = build_full_from_half(a_half)
                    h_full = normalize_to_sum_n(h_full)
                    best_ratio = compute_ratio(h_full)
                    improved_cycle = True

            # Alternation bisection polish (AB)
            a_half_candidate, r_candidate, imp = alternation_bisection_polish(
                a_half, best_ratio, max_iter=22, trust=0.18
            )
            if imp and r_candidate > best_ratio + 1e-12:
                a_half = project_monotone_nonincreasing(a_half_candidate)
                h_full = build_full_from_half(a_half)
                h_full = normalize_to_sum_n(h_full)
                best_ratio = compute_ratio(h_full)
                improved_cycle = True

            # TST sweep: siphon tiny mass from tails inward
            a_half_candidate, r_candidate, imp = tst_sweep(a_half, best_ratio, trust=0.08)
            if imp and r_candidate > best_ratio + 1e-12:
                a_half = project_monotone_nonincreasing(a_half_candidate)
                h_full = build_full_from_half(a_half)
                h_full = normalize_to_sum_n(h_full)
                best_ratio = compute_ratio(h_full)
                improved_cycle = True

            # Legacy CPE fallback: try m in {3, 2, 4} and accept the best
            for m in (3, 2, 4):
                a_star, r_star = line_search_cpe(a_half, m, best_ratio)
                if r_star > best_ratio + 1e-12:
                    a_half[0] -= m * a_star
                    for i in range(1, m + 1):
                        a_half[i] += a_star
                    a_half = project_monotone_nonincreasing(a_half)
                    h_full = build_full_from_half(a_half)
                    h_full = normalize_to_sum_n(h_full)
                    best_ratio = compute_ratio(h_full)
                    improved_cycle = True

            # Finalize this cycle
            a_half = project_monotone_nonincreasing(a_half)
            h_full = build_full_from_half(a_half)
            h_full = normalize_to_sum_n(h_full)
            cur_ratio = compute_ratio(h_full)
            if cur_ratio > best_ratio + 1e-14:
                best_ratio = cur_ratio
                improved_cycle = True

            if not improved_cycle:
                break

        # Final normalization and projection
        a_half = project_monotone_nonincreasing(a_half)
        h_full = build_full_from_half(a_half)
        h_full = normalize_to_sum_n(h_full)
        best_ratio = compute_ratio(h_full)
        return a_half, best_ratio

    # Run optimization on the top seeds
    best_heights = None
    best_ratio = -1.0
    for h0, _ in seed_pairs:
        # Extract half from h0 (mirror-consistent)
        a_half0 = np.zeros(half_len, dtype=float)
        a_half0[0] = h0[24]
        for k in range(1, half_len):
            a_half0[k] = h0[24 - k]
        a_half_opt, r_opt = optimize_half(a_half0)
        h_opt = build_full_from_half(a_half_opt)
        h_opt = normalize_to_sum_n(h_opt)
        r_val = compute_ratio(h_opt)
        if r_val > best_ratio:
            best_ratio = r_val
            best_heights = h_opt

    # Safety: Ensure final heights satisfy symmetry, monotone, nonnegativity, and sum normalization
    if best_heights is None:
        best_heights = np.ones(n, dtype=float)
    # Symmetry enforcement
    for i in range(n // 2):
        v = 0.5 * (best_heights[i] + best_heights[n - 1 - i])
        best_heights[i] = v
        best_heights[n - 1 - i] = v
    # Monotone enforcement on half
    a_half_final = np.zeros(half_len, dtype=float)
    a_half_final[0] = best_heights[24]
    for k in range(1, half_len):
        a_half_final[k] = best_heights[24 - k]
    a_half_final = project_monotone_nonincreasing(a_half_final)
    best_heights = build_full_from_half(a_half_final)
    best_heights = normalize_to_sum_n(best_heights)

    # Compute final ratio
    c_lower_bound = compute_ratio(best_heights)

    # Return as Python list and float
    return best_heights.tolist(), float(c_lower_bound)


# EVOLVE_END


if __name__ == "__main__":
    heights, c_lower_bound = optimize_lower_bound()
    print(json.dumps({"heights": heights, "c_lower_bound": c_lower_bound}))
```
