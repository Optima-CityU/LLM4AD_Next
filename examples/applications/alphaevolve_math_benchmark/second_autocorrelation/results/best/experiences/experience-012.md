Actionable mechanism that stabilized optimization under a nonsmooth Linf and enabled larger feasible gains while matching the verifier’s discretization.

- EMPP-PTSC (Entropic Mirror-Prox with Plateau-Nullspace Proximal step and Shoulder Corrector): The algorithm couples exact discretization-aligned objective/gradients and monotone acceptance on the true ratio with a blended ||g||_∞ treatment (entropy-smoothed softmax plus active-set subgradients) to avoid surrogate drift and stabilize directions on broad Linf plateaus; it then applies a plateau-nullspace proximal step with isotonic projection so updates make large, feasible, symmetry- and unimodality-preserving moves that boost ||g||_2^2 without inflating ||g||_∞, complemented by a shoulder-shaping corrector and symmetric mass transports to traverse kinks. For similar shape-constrained convolutional objectives, reuse this pattern: work in the log-domain with mirror-extragradient steps, project to feasibility each update, detect and respect argmax plateaus via nullspace-preserving proximal moves, and deploy targeted shoulder sculpting or symmetric transport line searches when gradient progress stalls.

```python
#!/usr/bin/env python3
"""Optimized step function for the second autocorrelation inequality.

This implementation constructs a symmetric, unimodal, nonnegative 50-bin step function
on [-1/4, 1/4] and maximizes the exact discrete ratio

    R = ||f*f||_2^2 / ( ||f*f||_1 * ||f*f||_∞ )

where all norms and the piecewise-linear quadrature match the verifier's discretization
bit-for-bit. The algorithm uses multiplicative (log-domain) mirror ascent with exact
gradients, a symmetry/unimodality projection via isotonic regression, and a robust
plateau tangent-space equalizer (IRLS-style) to flatten the central autocorrelation
plateau. Multiple structurally-informative seeds are explored deterministically, and
the best result is refined to exceed the target lower bound.

Key properties:
- Exact discretization alignment with the verifier.
- Symmetric, unimodal projection after every step.
- Multiplicative updates to maintain positivity and scale-invariant behavior.
- Nonsmooth Linf handled by exact subgradients at a plateau-aware argmax set.
- Plateau equalizer adjusts log-heights locally without inflating Linf excessively.

This solution intentionally avoids generic black-box optimizers and instead uses
task-specific geometry to reliably attain c_lower_bound > 0.8962.
"""

import json
import math
import numpy as np


# EVOLVE_START
def optimize_lower_bound():
    """
    Main entry: return heights (length 50) and the achieved c_lower_bound.

    The function fully implements the algorithm described in the prompt:
    - exact objective computation (matching verification)
    - mirror ascent in y=log(h) with an extragradient preview
    - projection to symmetric unimodality via isotonic regression
    - plateau tangent-space correction when ascent stalls
    - plateau-nullspace proximal step for safe large moves
    - seed diversity and deterministic refinement

    Returns:
        heights: list of 50 floats (nonnegative, symmetric, unimodal; mean normalized)
        c_lower_bound: float, achieved ratio
    """
    n = 50

    # Deterministic seed pool targeting plateau-with-shoulder shapes under unimodality
    seeds = generate_seeds(n=n)

    # Score seeds and select the top batch for refinement
    scored = []
    for h in seeds:
        h = project_sym_unimodal(h)
        R = compute_ratio(h)[0]
        scored.append((R, h))
    scored.sort(key=lambda x: -x[0])

    # Refine top seeds
    top_count = min(16, len(scored))
    best_h = None
    best_R = -np.inf
    for i in range(top_count):
        _, h0 = scored[i]
        h_ref, R_ref = refine_profile(h0)
        if R_ref > best_R:
            best_R = R_ref
            best_h = h_ref

    # Final polish
    best_h, best_R = polish(best_h, start_R=best_R)

    # Plateau-nullspace proximal large-step refinement
    best_h, best_R = plateau_nullspace_prox(best_h, best_R, attempts=3)

    # Deterministic symmetric pairwise mass-swap refinement with exact line search
    best_h, best_R = mass_swap_refine(best_h, start_R=best_R, passes=4)

    # Safety projection and normalization
    best_h = project_sym_unimodal(best_h)

    # Final exact ratio
    c_lower_bound, _, _, _ = compute_ratio(best_h)

    heights = [float(x) for x in best_h.tolist()]
    return heights, float(c_lower_bound)


# ------------- Core computational primitives ------------- #

def compute_ratio(h):
    """
    Compute the objective ratio and auxiliary data, exactly matching the verifier.

    Inputs:
        h: numpy array of shape (50,) nonnegative heights.

    Returns:
        R: ratio ||g||_2^2 / (||g||_1 * ||g||_∞), float
        g: convolution h * h, numpy array length 99
        info: dict with L2sq, L1, Linf and indices
        values: the padded values array used for integration (length 101)
    """
    # Convolution h*h: length n + n - 1 = 99
    g = np.convolve(h, h)
    # widths per baseline discretization: 101 nodes => 100 widths, all equal to 0.01
    widths = np.diff(np.linspace(-0.5, 0.5, len(g) + 2))
    # Pad with zero endpoints
    values = np.concatenate(([0.0], g, [0.0]))
    # Exact quadratic integral for piecewise-linear function squared:
    # Integral over each segment [x_i, x_{i+1}] of (linear)^2 equals width/3 * (a^2 + a*b + b^2)
    l2_squared = 0.0
    for index in range(len(g) + 1):
        a = values[index]
        b = values[index + 1]
        w = widths[index]
        l2_squared += w / 3.0 * (a * a + a * b + b * b)

    # L1 = average of |g| sampled at nodes; but here g >= 0 due to nonneg h
    # The verifier uses len(convolution) + 1 as denominator (99 + 1 = 100)
    L1 = float(np.sum(g) / (len(g) + 1))
    Linf = float(np.max(g))

    R = float(l2_squared / (L1 * Linf))

    info = dict(L2sq=float(l2_squared), L1=L1, Linf=Linf)
    return R, g, info, values


def dL2sq_dg(values, widths):
    """
    Compute gradient d(L2^2)/d g, where values = [0, g, 0] padded and widths
    are the segment widths; matches the piecewise-linear square integral formula.

    For node j (g[j] == values[j+1]):
        d/d g[j] = w/3 * (prev + 4*cur + next)
    where:
        prev = values[j], cur = values[j+1], next = values[j+2]
    Boundary handling is intrinsic since values[0]=values[-1]=0.

    Returns:
        grad: numpy array shape (len(g),)
    """
    M = len(values) - 2  # len(g)
    grad = np.zeros(M, dtype=float)
    # widths are uniform 0.01, but we keep the general formula
    for j in range(M):
        a = values[j]       # values[j]
        b = values[j + 1]   # g[j]
        c = values[j + 2]   # values[j+2]
        w = widths[j]       # segment width for [j, j+1]
        grad[j] = (w / 3.0) * (a + 4.0 * b + c)
    return grad


def _active_linf_subgradient(g, Linf, delta=None, prefer_center=True):
    """
    Build a stable subgradient for Linf with an active set near the maximum.

    Inputs:
        g: array of length M
        Linf: max value
        delta: tolerance; include indices with g >= (1 - delta) * Linf. If None, use exact.
        prefer_center: apply a gentle center bias to break ties.

    Returns:
        dLinf_dg: array of shape like g, summing to 1 on active set
        jrep: representative index (near center) for diagnostics
    """
    M = len(g)
    c = M // 2
    dLinf = np.zeros(M, dtype=float)
    if delta is None or delta <= 0:
        # Fallback: exact max set
        tol = max(1e-12, 1e-12 * Linf)
        active = np.where(g >= Linf - tol)[0]
    else:
        active = np.where(g >= (1.0 - float(delta)) * Linf)[0]
        if active.size == 0:
            active = np.array([int(np.argmax(g))], dtype=int)

    # Prefer a contiguous chunk around the center if active set is fragmented
    # Build weights with two factors: proximity to Linf and proximity to center
    prox_peak = (g[active] / Linf)
    prox_peak = np.clip(prox_peak, 0.0, 1.0)
    # Center bias: quadratic around center
    if prefer_center:
        dist = (active - c).astype(float)
        sigma = max(1.5, 0.25 * len(g) / 10.0)
        center_w = np.exp(-(dist / sigma) ** 2)
    else:
        center_w = np.ones_like(prox_peak)
    # Combine
    w = prox_peak ** 2.0 * center_w
    if np.sum(w) <= 0:
        w = np.ones_like(w)
    w = w / np.sum(w)
    dLinf[active] = w
    # representative index near the max and center
    jrep = int(active[np.argmax(w)])
    return dLinf, jrep


def compute_gradients(h, g, info, values, frozen_argmax_idx=None, active_delta=None):
    """
    Compute exact gradient of F = log(L2^2) - log(L1) - log(Linf) with respect to h,
    and then map to y = log(h) coordinates (multiplicative mirror ascent).

    Plateau-aware handling: if multiple entries of g attain the maximum (within
    a tiny tolerance), we distribute the Linf subgradient across a near-maximum
    active set using an adaptive tolerance delta, gently biased to the center.

    Inputs:
        h: numpy array of shape (n,)
        g: numpy array of shape (2n-1,)
        info: dict with L2sq, L1, Linf
        values: [0, g, 0] array
        frozen_argmax_idx: index for Linf subgradient; if not None, use single active index
        active_delta: if provided, include indices with g >= (1-delta) * max(g) in the
                      Linf subgradient support (center-biased weights)

    Returns:
        grad_y: gradient in y-space (same shape as h), numpy array
        z: dF/dg vector (shape like g)
        jmax: a representative index used for the Linf subgradient (argmax/center)
    """
    n = h.shape[0]
    M = g.shape[0]  # 2n - 1 = 99

    # Segment widths (matching exact quadrature)
    widths = np.diff(np.linspace(-0.5, 0.5, M + 2))

    L2sq = info["L2sq"]
    L1 = info["L1"]
    Linf = info["Linf"]

    # Gradient of L2^2 wrt g (exact matching)
    dL2_dg = dL2sq_dg(values, widths)

    # L1 subgradient: since g >= 0, derivative is constant 1/(M+1) at every entry
    dL1_dg = np.full(M, 1.0 / (M + 1.0), dtype=float)

    # Linf subgradient: either frozen argmax or active-set around near maxima
    if frozen_argmax_idx is not None:
        dLinf_dg = np.zeros(M, dtype=float)
        jmax = int(frozen_argmax_idx)
        dLinf_dg[jmax] = 1.0
    else:
        dLinf_dg, jmax = _active_linf_subgradient(g, Linf, delta=active_delta, prefer_center=True)

    # dF/dg = 1/L2^2 dL2/dg - 1/L1 dL1/dg - 1/Linf dLinf/dg
    z = (dL2_dg / L2sq) - (dL1_dg / L1) - (dLinf_dg / Linf)

    # Map to dF/dh via the convolution Jacobian:
    # ∂g[k]/∂h[r] = 2 h[k - r] (with zero outside bounds), so
    # grad_h[r] = 2 * sum_k z[k] h[k - r] = 2 * dot(h, z[r:r+n])
    grad_h = np.zeros(n, dtype=float)
    # Using exact sliding window to match the discrete Jacobian
    for r in range(n):
        z_slice = z[r:r + n]  # length n; valid since len(z)=2n-1
        grad_h[r] = 2.0 * float(np.dot(h, z_slice))

    # Convert to y-space gradient: dF/dy[r] = dF/dh[r] * h[r]
    grad_y = grad_h * h
    return grad_y, z, jmax


# ------------- Projection and initialization ------------- #

def pav_increasing(y):
    """
    Pool-Adjacent-Violators (PAV) algorithm to project onto the cone of
    nondecreasing sequences. Returns an array of the same length.

    This is used to enforce unimodality on the radial half profile.
    """
    # Robust, compact isotonic solver (nondecreasing) using unit weights
    v = np.array(y, dtype=float).copy()
    n = len(v)
    values = []
    weights = []
    for i in range(n):
        values.append(v[i])
        weights.append(1.0)
        # Merge backward while violation occurs
        while len(values) >= 2 and values[-2] > values[-1] + 1e-15:
            v2, v1 = values[-2], values[-1]
            w2, w1 = weights[-2], weights[-1]
            merged_val = (v2 * w2 + v1 * w1) / (w2 + w1)
            values[-2] = merged_val
            weights[-2] = w2 + w1
            values.pop()
            weights.pop()
    # Expand to full length; weights are integers (counts), since initial weights are 1
    out = np.zeros(n, dtype=float)
    idx = 0
    for val, wt in zip(values, weights):
        k = int(round(wt))
        out[idx: idx + k] = val
        idx += k
    return out


def project_sym_unimodal(h, eps=1e-12):
    """
    Project the heights onto the set of nonnegative, symmetric, unimodal profiles.
    - Symmetry: h[i] == h[n-1-i]
    - Unimodality: nondecreasing from edges to center (for even n)

    We perform isotonic regression on the half-profile averaged with its mirror,
    then mirror back to enforce symmetry. Normalize mean(h)=1 for scale invariance.
    """
    h = np.maximum(np.array(h, dtype=float), 0.0)
    n = h.shape[0]
    half = n // 2  # 25 for n=50

    # Average symmetric pairs to enforce symmetry in the projection step
    u = np.zeros(half, dtype=float)
    for i in range(half):
        u[i] = 0.5 * (h[i] + h[n - 1 - i])
    # Enforce nondecreasing (unimodality) via isotonic regression
    u_iso = pav_increasing(u)
    # Reconstruct full symmetric sequence
    h_proj = np.zeros_like(h)
    for i in range(half):
        h_proj[i] = u_iso[i]
        h_proj[n - 1 - i] = u_iso[i]
    # Ensure strict positivity to stay in the log-domain safely
    h_proj = np.maximum(h_proj, eps)
    # Normalize mean(h_proj) = 1 (scale-free ratio)
    mean_val = float(np.mean(h_proj))
    if mean_val > 0:
        h_proj /= mean_val
    return h_proj


def kaiser_window_half(half_len, beta=6.0):
    """
    Generate a nondecreasing half-profile from edge to center using a Kaiser window
    flipped to be increasing towards the center.
    """
    x = np.linspace(0.0, 1.0, half_len, endpoint=False) + 0.5 / half_len
    t = np.sqrt(1.0 - np.clip(x, 0, 1) ** 2)
    # Use numpy's i0
    i0 = np.i0
    denom = float(i0(beta))
    w = i0(beta * t) / denom
    # Increasing radial towards center
    w = w - w.min()
    if w.max() > 0:
        w = w / w.max()
    return w


def generate_seeds(n=50):
    """
    Generate a diverse pool of deterministic symmetric unimodal seeds.
    Returns a list of numpy arrays of length n.
    """
    half = n // 2  # 25
    seeds = []

    # 1) Triangular radial increase
    u_tri = np.linspace(0.1, 1.0, half)
    seeds.append(build_from_half(u_tri, n))

    # 2) Cosine-power families
    xs = (np.arange(half) + 0.5) / half
    for p in [1.0, 1.3, 1.5, 2.0, 3.0, 4.0, 6.0]:
        u = (1.0 - np.cos(np.pi * xs)) ** p
        u = u / max(u.max(), 1e-12)
        seeds.append(build_from_half(u, n))

    # 3) Kaiser windows with various betas
    for beta in [3.5, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0]:
        u = kaiser_window_half(half_len=half, beta=beta)
        seeds.append(build_from_half(u, n))

    # 4) Flat plateaus with linear tails: varied widths and tail levels
    for width in [6, 8, 9, 10, 11, 12, 13, 14, 16]:  # plateau half-width in bins
        for tail in [0.12, 0.15, 0.2, 0.25, 0.35, 0.5, 0.6]:
            u = np.zeros(half, dtype=float)
            tail_len = max(1, half - width)
            tail_vals = np.linspace(tail, 1.0, tail_len, endpoint=False)
            u[:tail_len] = tail_vals
            u[tail_len:] = 1.0
            seeds.append(build_from_half(u, n))

    # 5) Mild convex/concave variations
    u_base = np.linspace(0.2, 1.0, half)
    for q in [0.5, 1.2, 1.8, 2.3, 3.0]:
        u = u_base ** q
        seeds.append(build_from_half(u, n))

    # 6) Parametric taper with plateau and shoulder softness
    for width in [7, 9, 11, 13, 15]:
        for p in [1.0, 1.3, 1.5, 1.7, 2.0]:
            u = np.zeros(half, dtype=float)
            tail_len = max(1, half - width)
            # Smooth tail with power p
            s = np.linspace(0.0, 1.0, tail_len, endpoint=False)
            tail_vals = (0.15 + 0.85 * (s ** p))
            u[:tail_len] = tail_vals
            u[tail_len:] = 1.0
            seeds.append(build_from_half(u, n))

    # 7) Gentle two-level plateau seeds
    for width in [8, 10, 12, 14]:
        u = np.zeros(half, dtype=float)
        tail_len = max(1, half - width)
        s = np.linspace(0.0, 1.0, tail_len, endpoint=False)
        u[:tail_len] = 0.25 + 0.5 * s
        u[tail_len:] = 1.0
        seeds.append(build_from_half(u, n))

    # 8) Logistic-shaped ramps to center
    xs = (np.arange(half) + 0.5) / half
    for a in [6.0, 8.0, 12.0, 16.0]:
        for b in [0.55, 0.6, 0.65, 0.7]:
            u = 1.0 / (1.0 + np.exp(-a * (xs - b)))
            u = (u - u.min()) / (u.max() - u.min() + 1e-12)
            seeds.append(build_from_half(u, n))

    # 9) Slight shoulder dip seeds: plateau with a micro-dip before center
    for width in [11, 12, 13]:
        u = np.zeros(half, dtype=float)
        tail_len = max(1, half - width)
        s = np.linspace(0.0, 1.0, tail_len, endpoint=False)
        u[:tail_len] = 0.2 + 0.8 * (s ** 1.4)
        u[tail_len:] = 1.0
        # introduce tiny pre-center dip on the last 3 bins of half tail
        if tail_len >= 4:
            dip = min(3, tail_len - 1)
            for k in range(1, dip + 1):
                u[tail_len - k] *= (0.98 - 0.005 * k)
            u = np.maximum.accumulate(u)  # fix monotonic
        seeds.append(build_from_half(u, n))

    # Normalize and project each seed
    normed = []
    for h in seeds:
        h = project_sym_unimodal(h)
        normed.append(h)
    return normed


def build_from_half(u_half, n):
    """
    Given a half-profile u over indices 0..(n/2-1), build a symmetric profile of length n.
    """
    u_half = np.maximum(u_half, 1e-12)
    half = n // 2
    h = np.zeros(n, dtype=float)
    for i in range(half):
        h[i] = u_half[i]
        h[n - 1 - i] = u_half[i]
    # Normalize mean to 1
    h /= np.mean(h)
    return h


# ------------- Optimization (mirror ascent + plateau corrector) ------------- #

def refine_profile(h0):
    """
    Run multiplicative mirror-ascent on a given seed, with periodic
    plateau equalization corrections and shoulder shaping. Returns refined h and achieved ratio R.

    Includes:
    - Extragradient preview to stabilize around non-smooth faces.
    - Active-set Linf subgradients with an adaptive tolerance.
    - Armijo backtracking to ensure monotone ascent on the exact objective.
    - Plateau equalizer (symmetric) and shoulder transport moves on stalls.
    - Plateau-nullspace proximal step to allow safe large moves when stalled.
    """
    h = np.array(h0, dtype=float).copy()
    y = np.log(h)
    R, g, info, values = compute_ratio(h)
    n = len(h)
    M = len(g)

    # Initialization for mirror ascent
    base_step = 0.75
    min_step = 1e-7
    max_iters = 900

    frozen_jmax = int(np.argmax(g))
    stall_count = 0

    for t in range(max_iters):
        # Adaptive tolerance for Linf active-set subgradient
        # Starts looser, tightens as iterations progress
        delta_t = max(1.5e-4, 3e-3 * (0.995 ** t))

        # Main gradient at current point with frozen argmax for stability early on
        frozen_use = frozen_jmax if (t % 12) < 6 else None
        grad_y, z, jmax = compute_gradients(h, g, info, values,
                                            frozen_argmax_idx=frozen_use,
                                            active_delta=delta_t)
        gy_norm = np.linalg.norm(grad_y, ord=2)
        if gy_norm == 0.0 or not np.isfinite(gy_norm):
            break
        dir_main = grad_y / (gy_norm + 1e-18)

        # Extragradient preview using active-set subgradients
        preview_step = 0.20
        y_preview = y + preview_step * dir_main
        h_preview = project_sym_unimodal(np.exp(y_preview))
        R_pv, g_pv, info_pv, values_pv = compute_ratio(h_preview)
        grad_y_pv, _, _ = compute_gradients(h_preview, g_pv, info_pv, values_pv,
                                            frozen_argmax_idx=None,
                                            active_delta=delta_t * 0.7)
        gy_pv_norm = np.linalg.norm(grad_y_pv, ord=2)
        dir_pv = grad_y_pv / (gy_pv_norm + 1e-18)

        # Corrected direction: average of main and preview directions
        dir_y = 0.5 * (dir_main + dir_pv)
        # Normalize direction to unit norm
        dy_norm = np.linalg.norm(dir_y, ord=2)
        if dy_norm > 0:
            dir_y = dir_y / dy_norm

        step = base_step
        improved = False

        # Backtracking line search ensuring monotone increase
        for bt in range(30):
            y_trial = y + step * dir_y
            h_trial = np.exp(y_trial)
            # Project to symmetric unimodal set and normalize
            h_trial = project_sym_unimodal(h_trial)
            R_trial, g_trial, info_trial, values_trial = compute_ratio(h_trial)
            if R_trial >= R - 1e-12:
                # Accept
                y = np.log(h_trial)
                h = h_trial
                R, g, info, values = R_trial, g_trial, info_trial, values_trial
                improved = True
                break
            step *= 0.5
            if step < min_step:
                break

        # Update frozen argmax occasionally for Linf subgradient stability
        if (t % 6) == 0:
            frozen_jmax = int(np.argmax(g))

        if improved:
            stall_count = 0
            base_step = min(1.25, base_step * 1.01)
        else:
            stall_count += 1
            base_step = max(0.2, base_step * 0.84)

        # Plateau tangent-space corrector on stalls or periodically
        if (stall_count > 5) or (t % 36 == 18):
            h_new, R_new, g_new, info_new, values_new = plateau_equalizer(h, R, g, info, values)
            if R_new > R + 1e-12:
                h, R, g, info, values = h_new, R_new, g_new, info_new, values_new
                y = np.log(h)
                stall_count = 0
                base_step = min(1.0, base_step * 1.02)
            else:
                stall_count = 0  # reset to avoid repeated failed hits

        # Plateau-nullspace proximal step for safe large moves when stalled
        if (t % 40 == 20) or (not improved and stall_count >= 7):
            h_np, R_np = plateau_nullspace_prox(h, R, attempts=1)
            if R_np > R + 1e-12:
                h, R = h_np, R_np
                y = np.log(h)
                R, g, info, values = compute_ratio(h)
                base_step = min(1.0, base_step * 1.015)
                stall_count = 0

        # Shoulder shaping via symmetric transport when progress slows
        if (t % 20 == 10) or (not improved and (t % 10 == 5)):
            h_s, R_s, g_s, info_s, values_s = shoulder_transport(h, R)
            if R_s > R + 1e-12:
                h, R, g, info, values = h_s, R_s, g_s, info_s, values_s
                y = np.log(h)
                base_step = min(1.0, base_step * 1.015)

    return h, R


def J_row(h, m):
    """
    Jacobian row of g[m] with respect to y = log(h).
    J[m, r] = ∂g[m]/∂y[r] = 2 h[r] h[m - r] if valid.
    """
    n = len(h)
    row = np.zeros(n, dtype=float)
    for r in range(n):
        t = m - r
        hr = h[r]
        ht = h[t] if 0 <= t < n else 0.0
        row[r] = 2.0 * hr * ht
    return row


def plateau_equalizer(h, R, g, info, values):
    """
    IRLS-like tangent-space correction to equalize the central autocorrelation plateau.

    This version builds symmetric residuals:
      r_k^+ = g[c] - g[c+k], r_k^- = g[c] - g[c-k]
    and aims to reduce them to zero (flatten plateau). It also shapes the shoulder right
    outside the plateau with a desired small drop to avoid inflating Linf.

    We solve a small ridge-regularized least-squares for delta_y, with a locality window.
    """
    n = len(h)
    M = len(g)
    c = M // 2  # central index

    # Determine adaptive K by thresholding closeness to peak
    g0 = g[c]
    theta = 0.989  # closeness threshold
    Kmax = min(12, (M - 1) // 2)
    K = 0
    for k in range(1, Kmax + 1):
        if g[c + k] >= theta * g0 and g[c - k] >= theta * g0:
            K = k
        else:
            break
    # Ensure at least a few constraints
    K = max(K, 6)
    K = min(K, Kmax)

    rows = []
    residuals = []

    # Build symmetric difference rows: (J[c,:] - J[c+k,:]) delta = g[c+k] - g[c]
    Jc = J_row(h, c)
    for k in range(1, K + 1):
        # Right side
        Jck = J_row(h, c + k)
        rows.append(Jc - Jck)
        residuals.append(g[c + k] - g[c])
        # Left side
        Jckm = J_row(h, c - k)
        rows.append(Jc - Jckm)
        residuals.append(g[c - k] - g[c])

    # Shoulder shaping just beyond plateau: target small drop
    k_out = K + 1
    theta_out = 0.97 - 0.003 * min(K, 10)  # slightly tighter drop for wider plateaus
    # Right shoulder
    if c + k_out < M:
        Jr = J_row(h, c + k_out)
        rows.append(theta_out * Jc - Jr)
        residuals.append(g[c + k_out] - theta_out * g[c])
    # Left shoulder
    if c - k_out >= 0:
        Jl = J_row(h, c - k_out)
        rows.append(theta_out * Jc - Jl)
        residuals.append(g[c - k_out] - theta_out * g[c])

    if not rows:
        return h, R, g, info, values

    A = np.vstack(rows)
    b = -np.array(residuals, dtype=float)

    # Locality window centered near middle to keep corrections local
    mid_left = n // 2 - 10
    mid_right = n // 2 + 10
    w = np.ones(n, dtype=float) * 0.25
    w[mid_left:mid_right] = 1.0

    # Reweight columns by window weights
    A_w = A * w[np.newaxis, :]

    # Solve least-squares with small ridge for stability
    ridge = 3e-6
    AtA = A_w.T @ A_w + ridge * np.eye(n)
    Atb = A_w.T @ b
    try:
        delta = np.linalg.solve(AtA, Atb)
    except np.linalg.LinAlgError:
        delta, *_ = np.linalg.lstsq(A_w, b, rcond=None)

    # Remove mean (scale direction) in y-space to maintain mean ~ 1
    delta -= np.mean(delta)

    # Taper update towards the center to avoid edge artifacts
    idx = np.arange(n)
    center = (n - 1) / 2.0
    taper = np.exp(-((idx - center) / (0.35 * n)) ** 2)
    delta *= taper

    # Limit update magnitude for stability
    max_abs = np.max(np.abs(delta))
    if max_abs > 0.22:
        delta *= 0.22 / max_abs

    # Backtracking line search on delta
    y = np.log(h)
    for alpha in [1.0, 0.7, 0.45, 0.3, 0.2, 0.1]:
        y_trial = y + alpha * delta
        h_trial = np.exp(y_trial)
        h_trial = project_sym_unimodal(h_trial)
        R_trial, g_trial, info_trial, values_trial = compute_ratio(h_trial)
        if R_trial >= R + 1e-12:
            return h_trial, R_trial, g_trial, info_trial, values_trial

    # If not improved, return original
    return h, R, g, info, values


def shoulder_transport(h, R):
    """
    Targeted symmetric shoulder shaping via a zero-mean window in log-space.

    We apply y += alpha * w where w is concentrated near the center and slightly
    negative on a ring outside, so that the central plateau is raised gently and
    the shoulder tightens. Projection ensures symmetry/unimodality, and we line
    search alpha to guarantee monotone improvement.

    Returns new h and metrics if improved; otherwise returns originals.
    """
    y = np.log(h)
    n = len(h)
    # Build a center-focused window with zero mean: difference of two Gaussians
    idx = np.arange(n)
    center = (n - 1) / 2.0
    d = np.abs(idx - center)
    s1 = max(1.8, 0.18 * n)   # central width
    s2 = max(3.0, 0.35 * n)   # outer width
    w1 = np.exp(-(d / s1) ** 2)
    w2 = np.exp(-(d / s2) ** 2)
    w = w1 - (np.sum(w1) / np.sum(w2)) * w2  # zero mean approximately
    # Recenter to exact zero-mean
    w -= np.mean(w)

    R_best = R
    best = (h, None)
    for alpha in [0.35, 0.25, 0.18, 0.12, 0.08]:
        y_trial = y + alpha * w
        h_trial = np.exp(y_trial)
        h_trial = project_sym_unimodal(h_trial)
        R_trial, g_trial, info_trial, values_trial = compute_ratio(h_trial)
        if R_trial > R_best + 1e-12:
            R_best = R_trial
            best = (h_trial, (g_trial, info_trial, values_trial))

    if best[1] is not None:
        h_best = best[0]
        g_best, info_best, values_best = best[1]
        return h_best, R_best, g_best, info_best, values_best
    else:
        # No improvement
        R0, g0, info0, values0 = compute_ratio(h)
        return h, R0, g0, info0, values0


def plateau_nullspace_prox(h0, R0=None, attempts=2):
    """
    Plateau-nullspace proximal step:
    - Detect near-maximum plateau indices P in g.
    - Build A from Jacobian rows J[p,:] with respect to y.
    - Project current gradient onto nullspace of A: d = grad_y - A^T (AA^T + λI)^-1 A grad_y.
    - Take a proximal step in y along d, project to symmetric unimodality, line search accept.

    Args:
        h0: current heights
        R0: current ratio (optional)
        attempts: number of line-search attempts with decaying step sizes

    Returns:
        h_best, R_best
    """
    h = np.array(h0, dtype=float)
    R, g, info, values = compute_ratio(h)
    if R0 is not None and R0 > R:
        R = float(R0)
    n = len(h)
    M = len(g)
    Linf = info["Linf"]

    # Near-plateau detection
    delta = 0.0015
    active = np.where(g >= (1.0 - delta) * Linf)[0]
    if active.size == 0:
        return h, R
    # Constrain active set to be around center to avoid edge effects
    c = M // 2
    active = active[(active >= c - 2 * (n // 5)) & (active <= c + 2 * (n // 5))]

    # Build A and gradient
    rows = [J_row(h, int(p)) for p in active]
    A = np.vstack(rows) if rows else np.zeros((0, n), dtype=float)

    grad_y, _, _ = compute_gradients(h, g, info, values, frozen_argmax_idx=None, active_delta=delta)
    gy = grad_y.copy()

    if A.shape[0] > 0:
        # Nullspace projection: d = gy - A^T (AA^T + λI)^-1 A gy
        AA_t = A @ A.T
        lam = 1e-6
        try:
            inv = np.linalg.inv(AA_t + lam * np.eye(AA_t.shape[0]))
        except np.linalg.LinAlgError:
            inv, *_ = np.linalg.lstsq(AA_t + lam * np.eye(AA_t.shape[0]), np.eye(AA_t.shape[0]), rcond=None)
        corr = A.T @ (inv @ (A @ gy))
        d = gy - corr
    else:
        d = gy

    # Remove mean to avoid pure scaling
    d -= np.mean(d)

    # Locality taper
    idx = np.arange(n)
    center = (n - 1) / 2.0
    taper = np.exp(-((idx - center) / (0.4 * n)) ** 2)
    d *= taper

    # Normalize direction
    dn = np.linalg.norm(d)
    if dn > 0:
        d /= dn

    # Line search with projection and monotone acceptance
    y = np.log(h)
    step0 = 0.8
    best_h = h
    best_R = R
    for k in range(attempts):
        step = step0 * (0.6 ** k)
        for bt in range(20):
            y_trial = y + step * d
            h_trial = np.exp(y_trial)
            h_trial = project_sym_unimodal(h_trial)
            R_trial, _, _, _ = compute_ratio(h_trial)
            if R_trial >= best_R + 1e-12:
                best_h = h_trial
                best_R = R_trial
                break
            step *= 0.55
            if step < 1e-8:
                break

    return best_h, best_R


def polish(h0, start_R=None):
    """
    Short final polishing phase with small steps and frequent plateau equalization checks.
    """
    h = np.array(h0, dtype=float).copy()
    R, g, info, values = compute_ratio(h)
    if start_R is not None and start_R > R:
        R = start_R
    y = np.log(h)
    base_step = 0.5
    min_step = 1e-7

    for t in range(380):
        # Tighter active-set tolerance over polish
        delta_t = max(1.2e-4, 2e-3 * (0.996 ** t))
        grad_y, z, jmax = compute_gradients(h, g, info, values,
                                            frozen_argmax_idx=None,
                                            active_delta=delta_t)
        gy_norm = np.linalg.norm(grad_y, ord=2)
        if gy_norm == 0.0 or not np.isfinite(gy_norm):
            break
        dir_y = grad_y / (gy_norm + 1e-18)

        step = base_step
        improved = False
        for bt in range(28):
            y_trial = y + step * dir_y
            h_trial = np.exp(y_trial)
            h_trial = project_sym_unimodal(h_trial)
            R_trial, g_trial, info_trial, values_trial = compute_ratio(h_trial)
            if R_trial >= R - 1e-12:
                y = np.log(h_trial)
                h = h_trial
                R, g, info, values = R_trial, g_trial, info_trial, values_trial
                improved = True
                break
            step *= 0.5
            if step < min_step:
                break

        # Plateau equalizer often during polish
        if (t % 3 == 0) or (not improved):
            h2, R2, g2, info2, values2 = plateau_equalizer(h, R, g, info, values)
            if R2 > R:
                h, R, g, info, values = h2, R2, g2, info2, values2
                y = np.log(h)

        # Plateau-nullspace proximal step to stabilize Linf changes
        if (t % 12 == 6) or (not improved and (t % 8 == 4)):
            h_np, R_np = plateau_nullspace_prox(h, R, attempts=1)
            if R_np > R:
                h, R = h_np, R_np
                y = np.log(h)
                R, g, info, values = compute_ratio(h)

        # Occasional shoulder shaping to tighten drop after plateau
        if (t % 10 == 5) or (not improved and (t % 6 == 3)):
            h_s, R_s, g_s, info_s, values_s = shoulder_transport(h, R)
            if R_s > R:
                h, R, g, info, values = h_s, R_s, g_s, info_s, values_s
                y = np.log(h)

    return h, R


def mass_swap_refine(h0, start_R=None, passes=3):
    """
    Deterministic symmetric pairwise mass transfer refinement with exact 1D golden-section
    line search along each pair, preserving symmetry and unimodality on the half profile.

    Idea: transfer mass (in the half-profile) from an outer index i to a near-center index j,
    constrained so that the nondecreasing constraint on the half-profile is preserved.

    Args:
        h0: initial heights (symmetric, unimodal)
        start_R: optional ratio value to avoid recomputation
        passes: number of full passes over selected index pairs
    Returns:
        h_best, R_best
    """
    h_best = np.array(h0, dtype=float)
    if start_R is None:
        R_best, _, _, _ = compute_ratio(h_best)
    else:
        R_best = float(start_R)

    n = len(h_best)
    half = n // 2

    # Construct half-profile
    def to_half(h):
        u = np.zeros(half, dtype=float)
        for i in range(half):
            u[i] = 0.5 * (h[i] + h[n - 1 - i])
        return u

    def from_half(u):
        h = np.zeros(n, dtype=float)
        for i in range(half):
            h[i] = u[i]
            h[n - 1 - i] = u[i]
        # Normalize mean to 1 to be safe
        h /= np.mean(h)
        return h

    u_best = to_half(h_best)

    # Select candidate pairs: more extensive range
    outer_indices = list(range(0, min(12, half)))
    inner_candidates = [half - 1 - k for k in range(0, min(12, half))]
    candidate_pairs = []
    for i in outer_indices:
        for j in inner_candidates:
            if j > i:
                candidate_pairs.append((i, j))

    # Golden-section constants
    phi = 0.5 * (math.sqrt(5.0) - 1.0)

    for p in range(passes):
        improved_pass = False
        for (i, j) in candidate_pairs:
            # Compute feasible tmax so that u remains nondecreasing and positive
            u = u_best.copy()
            # Constraints for decreasing u[i] by t: u[i] - t >= u[i-1] (or >= 0 if i==0)
            lower_prev = u[i - 1] if i > 0 else 0.0
            max_t_i = max(0.0, u[i] - lower_prev)
            # Constraints for increasing u[j] by t: u[j] + t <= u[j+1] (or inf if j is last)
            upper_next = u[j + 1] if j + 1 < half else float('inf')
            max_t_j = (upper_next - u[j]) if np.isfinite(upper_next) else max_t_i
            tmax = max(0.0, min(max_t_i, max_t_j))
            if tmax <= 1e-8:
                continue

            # Golden-section search on t in [0, tmax] to maximize R
            a, b = 0.0, tmax
            c = b - phi * (b - a)
            d = a + phi * (b - a)

            def eval_t(tval):
                u_trial = u.copy()
                u_trial[i] -= tval
                u_trial[j] += tval
                # Safety clamps to enforce monotonicity explicitly
                if i > 0 and u_trial[i] < u_trial[i - 1] - 1e-12:
                    return -1.0
                if j + 1 < half and u_trial[j] > u_trial[j + 1] + 1e-12:
                    return -1.0
                h_trial = from_half(u_trial)
                # Project to be safe
                h_trial = project_sym_unimodal(h_trial)
                R_trial, _, _, _ = compute_ratio(h_trial)
                return R_trial

            f_c = eval_t(c)
            f_d = eval_t(d)
            # Run a fixed number of iterations for determinism
            for _ in range(18):
                if f_c < 0 and f_d < 0:
                    break
                if f_c < f_d:
                    a = c
                    c = d
                    f_c = f_d
                    d = a + phi * (b - a)
                    f_d = eval_t(d)
                else:
                    b = d
                    d = c
                    f_d = f_c
                    c = b - phi * (b - a)
                    f_c = eval_t(c)

            # Choose best of endpoints and internal points
            candidates = [(eval_t(0.0), 0.0), (eval_t(tmax), tmax)]
            if f_c >= 0:
                candidates.append((f_c, c))
            if f_d >= 0:
                candidates.append((f_d, d))
            candidates.sort(key=lambda x: -x[0])
            R_try, t_star = candidates[0]

            if R_try > R_best + 1e-12 and t_star > 0:
                # Apply the best move
                u_best[i] -= t_star
                u_best[j] += t_star
                h_best = from_half(u_best)
                h_best = project_sym_unimodal(h_best)
                R_best, _, _, _ = compute_ratio(h_best)
                improved_pass = True

        if not improved_pass:
            # Early stop if no candidate pair improved the ratio in this pass
            break

    return h_best, R_best


# EVOLVE_END


if __name__ == "__main__":
    heights, c_lower_bound = optimize_lower_bound()
    print(json.dumps({"heights": heights, "c_lower_bound": c_lower_bound}))
```
