Actionable design insight grounded in the algorithm description, metrics, generation evidence, and implementation for minimizing the hard max correlate(s, 1−s, 'full') under symmetry and mass constraints.

- CF-TK+PS-AS: Coarse-to-Fine Unimodal Softmax with Plateau-Safe Projection and Top‑K/Active‑Set Polish: Apply a plateau-safe unimodality projection (isotonic regression plus capped-simplex with plateau correction) after every update within a symmetry-aware half-parameterization that pins the center to 0.5, which removes re-projection ripples, preserves feasibility, and keeps smoothed softmax descent aligned with the verifier’s hard max. Combine coarse-to-fine resolution and temperature annealing with Top‑K Armijo polishing followed by an active-set subgradient phase focused on the worst shifts to shave tied peaks without oscillation; this pattern produced a valid sequence with upper bound 0.3820605468557273 (validity 1.0, score 0.9970330701113838) in Generation 3.

```python
#!/usr/bin/env python3
"""Optimization-based sequence generator for Erdős' minimum overlap problem.

This implements a structured, projected optimization on a symmetric discrete step
function h over [0, 2], represented by a half-sequence that is mirrored as in the
usage example. The objective minimizes a smoothed maximum (softmax) of the discrete
overlap sums sum_i h_i (1 - h_{i+s}) across all integer shifts s, which corresponds
to the compute_upper_bound procedure used externally.

We enforce feasibility at all times:
- Values are clamped to [0, 1]
- The last coefficient of the half-sequence is fixed to 0.5 so that after mirroring
  the final sequence has exact mass n/2
- The sum of the half-sequence equals m/2 via a water-filling (boxed simplex) projection.
- NEW: Enforce unimodality (nondecreasing) on the first m-1 entries (half-sequence
  excluding the center) via isotonic regression (Pool Adjacent Violators) as a projection
  step. This keeps the search inside the symmetric unimodal family which empirically
  lowers the maximum overlap.

Two improvements added:
1) Unimodality projection on the half-sequence after each gradient step: After the Adam
   update and capped-simplex projection on the first m-1 coordinates (with x[-1] fixed
   at 0.5), enforce nondecreasing monotonicity on x[:-1] using isotonic regression, then
   re-project onto the capped simplex to restore the sum constraint and clamp to [0,1].
2) Argmax polishing: After the annealed softmax stages, identify the active shift s*
   that maximizes the discrete overlap on the finalized full sequence. Run a brief
   projected subgradient descent minimizing F_{s*}(h) with the exact gradient
   ∂F_{s*}/∂y_j = (1−y_{j+s*}) − y_{j−s*} mapped back to the half-sequence, enforcing the
   unimodal capped-simplex constraints after each step. Recompute s* periodically and
   switch if the argmax changes.

These adjustments empirically reduce the resulting upper bound below the previous
construction threshold.
"""

import json
import math
from typing import Tuple

import numpy as np


# EVOLVE_START
def _finalize_full_sequence(half: np.ndarray) -> np.ndarray:
    """Construct the final symmetric sequence from a half-sequence.

    The external code forms final_sequence = concatenate(half[:-1], half[::-1]).
    We use the same construction for objective evaluation during optimization.
    """
    return np.concatenate((half[:-1], half[::-1]))


def _compute_overlap_values(y: np.ndarray) -> np.ndarray:
    """Compute discrete overlap sums F[s] = sum_i y_i (1 - y_{i+s}) for all integer
    shifts s in the range [-(n-1), ..., +(n-1)].

    This matches the values produced by np.correlate(y, 1 - y, mode='full'), but is
    implemented explicitly for ease of gradient handling and correctness of indexing.

    Returns:
        F: np.ndarray of length 2n - 1 with F[n-1 + s] corresponding to shift s.
    """
    n = len(y)
    F = np.zeros(2 * n - 1, dtype=np.float64)
    # Positive shifts s >= 0
    for s in range(0, n):
        # Overlap indices: i in [0, n - 1 - s]
        a = y[: n - s]
        b = y[s:]
        F[n - 1 + s] = np.sum(a) - np.sum(a * b)
    # Negative shifts s = -t, t in [1, n-1]
    for t in range(1, n):
        # Overlap indices: i in [t, n-1]
        a = y[t:]
        b = y[: n - t]
        F[n - 1 - t] = np.sum(a) - np.sum(a * b)
    return F


def _softmax_objective_and_grad_y(y: np.ndarray, tau: float) -> Tuple[float, np.ndarray]:
    """Compute softmax-smoothed objective and its gradient with respect to y.

    Objective approximates max_s F[s] / (n * 2) with:
        obj_tau = (1/tau) * log sum_s exp(tau * F[s]) / (n * 2)

    Gradient wrt y is the softmax-weighted average of grad F[s], scaled by 1/(n*2).

    Returns:
        obj: scalar objective value
        grad_y: gradient vector of length len(y)
    """
    n = len(y)
    F = _compute_overlap_values(y)
    # Numerically stable softmax weights
    z = tau * F
    z_max = np.max(z)
    w = np.exp(z - z_max)
    w_sum = np.sum(w)
    w /= w_sum

    # Objective value
    obj = (math.log(w_sum) + z_max) / tau / (n * 2.0)

    # Gradient wrt y by vectorized slice accumulation
    grad_y = np.zeros_like(y, dtype=np.float64)
    # Positive shifts s >= 0 -> index k = n - 1 + s
    for s in range(0, n):
        k = n - 1 + s
        wk = w[k]
        # Contributions on positions 0..n-s-1: +1 - y[s:]
        grad_y[: n - s] += wk * (1.0 - y[s:])
        # Contributions on positions s..n-1: - y[:n-s]
        grad_y[s:] -= wk * y[: n - s]

    # Negative shifts s = -t, t in [1, n-1] -> index k = n - 1 - t
    for t in range(1, n):
        k = n - 1 - t
        wk = w[k]
        # Contributions on positions t..n-1: +1 - y[:n-t]
        grad_y[t:] += wk * (1.0 - y[: n - t])
        # Contributions on positions 0..n-t-1: - y[t:]
        grad_y[: n - t] -= wk * y[t:]

    # Scale gradient due to final normalization by 1 / (n * 2)
    grad_y /= (n * 2.0)
    return obj, grad_y


def _map_grad_y_to_half(grad_y: np.ndarray, m: int) -> np.ndarray:
    """Map gradient from full sequence y to half sequence x.

    Final y is built from half x as: y = concat(x[:-1], x[::-1])
    So:
      - For j in [0, m-2], x_j appears twice in y at indices j and (2m-2 - j).
      - For j = m-1, x_{m-1} appears once in y at index m-1.

    Sum the corresponding gradient contributions.
    """
    n = 2 * m - 1
    assert len(grad_y) == n
    grad_x = np.zeros(m, dtype=np.float64)
    # j in [0..m-2]
    for j in range(m - 1):
        grad_x[j] = grad_y[j] + grad_y[n - 1 - j]
    # center element j = m - 1
    grad_x[m - 1] = grad_y[m - 1]
    return grad_x


def _project_boxed_simplex(v: np.ndarray, target_sum: float, lower: float = 0.0, upper: float = 1.0) -> np.ndarray:
    """Project vector v onto the capped simplex:
        { x in [lower, upper]^d : sum(x) = target_sum }

    Uses bisection on the Lagrange multiplier lambda for x = clip(v - lambda, lower, upper).
    """
    d = len(v)
    # Handle trivial bounds
    lower = float(lower)
    upper = float(upper)
    # If target is outside feasible bounds, clamp to closest feasible
    min_sum = d * lower
    max_sum = d * upper
    if target_sum <= min_sum:
        return np.full(d, lower, dtype=np.float64)
    if target_sum >= max_sum:
        return np.full(d, upper, dtype=np.float64)

    # Bracket lambda: moving lambda smaller increases x (toward upper), larger decreases x (toward lower).
    lam_lo = np.min(v) - upper  # makes most coordinates at upper bound
    lam_hi = np.max(v) - lower  # makes most coordinates at lower bound

    # Bisection
    for _ in range(60):  # sufficient precision for double
        lam_mid = 0.5 * (lam_lo + lam_hi)
        x = v - lam_mid
        # Clip to [lower, upper]
        x = np.clip(x, lower, upper)
        s = x.sum()
        if s > target_sum:
            # Need to reduce sum -> increase lambda
            lam_lo = lam_mid
        else:
            lam_hi = lam_mid
    lam = 0.5 * (lam_lo + lam_hi)
    x = np.clip(v - lam, lower, upper)
    return x


def _adam_update(x: np.ndarray, grad: np.ndarray, opt_state: dict, lr: float) -> np.ndarray:
    """One Adam optimizer update step."""
    beta1 = opt_state.get("beta1", 0.9)
    beta2 = opt_state.get("beta2", 0.999)
    eps = opt_state.get("eps", 1e-8)
    t = opt_state.get("t", 0) + 1

    m = opt_state.get("m", np.zeros_like(x))
    v = opt_state.get("v", np.zeros_like(x))

    m = beta1 * m + (1.0 - beta1) * grad
    v = beta2 * v + (1.0 - beta2) * (grad * grad)

    m_hat = m / (1.0 - beta1 ** t)
    v_hat = v / (1.0 - beta2 ** t)

    x_new = x - lr * m_hat / (np.sqrt(v_hat) + eps)

    opt_state["m"] = m
    opt_state["v"] = v
    opt_state["t"] = t
    return x_new


def _compute_upper_bound_from_half(half: np.ndarray) -> float:
    """Compute the external upper bound for a given half-sequence using the
    same procedure as in the provided compute_upper_bound function."""
    y = _finalize_full_sequence(half)
    conv_vals = np.correlate(y, 1.0 - y, mode="full")
    return np.max(conv_vals) / len(y) * 2.0


def _isotonic_non_decreasing(y: np.ndarray) -> np.ndarray:
    """Pool Adjacent Violators algorithm (PAV) for L2 isotonic regression
    enforcing nondecreasing order.

    Returns the projection of y onto the set {x: x[0] <= x[1] <= ... } minimizing L2.
    """
    n = len(y)
    # Initialize blocks with unit weights
    # Each block stores (weight_sum, weighted_value_sum)
    blocks_w = []
    blocks_wv = []
    for i in range(n):
        wi = 1.0
        wvi = wi * y[i]
        blocks_w.append(wi)
        blocks_wv.append(wvi)
        # Merge adjacent blocks if monotonicity violated
        while len(blocks_w) >= 2:
            w1 = blocks_w[-2]
            w2 = blocks_w[-1]
            a1 = blocks_wv[-2] / w1
            a2 = blocks_wv[-1] / w2
            if a1 > a2:
                # Merge last two blocks
                blocks_w[-2] = w1 + w2
                blocks_wv[-2] = blocks_wv[-2] + blocks_wv[-1]
                blocks_w.pop()
                blocks_wv.pop()
            else:
                break
    # Expand blocks back to a full vector with fitted block means
    res = np.empty(n, dtype=np.float64)
    idx = 0
    for bw, bwv in zip(blocks_w, blocks_wv):
        val = bwv / bw
        cnt = int(round(bw))  # bw should be an integer since all weights were 1.0
        # Safety: ensure at least 1
        cnt = max(1, cnt)
        res[idx : idx + cnt] = val
        idx += cnt
    return res


def _single_shift_grad_y(y: np.ndarray, s: int) -> np.ndarray:
    """Compute exact gradient of F[s] = sum_i y_i (1 - y_{i+s}) with respect to y.

    Handles both positive and negative shifts s.
    """
    n = len(y)
    grad = np.zeros_like(y)
    if s >= 0:
        if s < n:
            grad[: n - s] += (1.0 - y[s:])
            grad[s:] -= y[: n - s]
    else:
        t = -s
        if t < n:
            grad[t:] += (1.0 - y[: n - t])
            grad[: n - t] -= y[t:]
    return grad


def _resample_half_linear(x_old: np.ndarray, m_new: int) -> np.ndarray:
    """Resample a half sequence to a new length using linear interpolation.

    Ensures the last element (center) remains exactly 0.5, but mass will be corrected
    by projection afterwards.
    """
    m_old = len(x_old)
    if m_new == m_old:
        return x_old.copy()
    # Parameterize both old and new grids on [0, 1]
    xp_old = np.linspace(0.0, 1.0, num=m_old)
    xp_new = np.linspace(0.0, 1.0, num=m_new)
    x_new = np.interp(xp_new, xp_old, x_old)
    # Ensure strict feasibility constraints will be re-enforced later
    x_new[-1] = 0.5
    x_new = np.clip(x_new, 0.0, 1.0)
    return x_new


def generate_erdos_data() -> np.ndarray:
    """Generates a sequence of coefficients for Erdős' minimum overlap problem.

    CF-TK+PS-AS: Coarse-to-Fine annealed optimization with unimodal projection, and
    Top-K + Active-set polishing with Armijo backtracking.

    - Half-sequence parameterization with pinned center 0.5.
    - Capped-simplex projection for exact mass and box constraints.
    - Isotonic projection for unimodality on x[:-1].
    - Smoothed min-max optimization with temperature continuation and Adam.
    - Coarse-to-fine resolution schedule with symmetric linear upsampling.
    - Top-k polishing and active-set polishing on the exact verifier objective.

    Returns:
        np.ndarray: Best found half-sequence (values in [0,1], sum = m/2, last=0.5).
    """
    # Coarse-to-fine schedule of half lengths (final full n = 2*m − 1)
    # Slightly higher final resolution improves polishing stability.
    schedule_ms = [96, 144, 192]

    # Temperature (softmax) schedule, from smooth to sharp
    tau_schedule = [1.0, 3.0, 10.0, 30.0, 100.0, 300.0, 900.0]
    iters_per_tau = 50
    base_lr = 0.05  # Adam base step; scaled by sqrt(tau)

    # Polishing configuration (Top-K and Active-Set with Armijo backtracking)
    topk_k = 3
    topk_steps = 40
    topk_lr_init = 0.06
    topk_armijo_beta = 0.5
    topk_recompute_every = 2

    # Active set polishing (shifts within tol of current max)
    active_tol_ratio = 1e-3  # within 0.1% of max gets included
    active_steps = 35
    active_lr_init = 0.05
    active_armijo_beta = 0.5
    active_recompute_every = 1

    # Single-shift final shaving
    single_steps = 30
    single_lr_init = 0.05
    single_armijo_beta = 0.5
    single_recompute_every = 1

    # RNG for reproducible inits
    rng = np.random.default_rng(12345)

    def project_half(x: np.ndarray, last_fixed: float) -> np.ndarray:
        """Project onto feasible set: [0,1]^m, x[-1]=last_fixed, sum(x)=m/2, enforce unimodality."""
        m = len(x)
        sum_target_half = m / 2.0
        sum_target_rest = sum_target_half - last_fixed
        x = np.asarray(x, dtype=np.float64)
        x = np.clip(x, 0.0, 1.0)
        x[-1] = last_fixed
        # Isotonic regression on head (monotone nondecreasing)
        head = x[:-1]
        head_iso = _isotonic_non_decreasing(head)
        head_iso = np.clip(head_iso, 0.0, 1.0)
        # Project to capped simplex with exact sum for head
        head_proj = _project_boxed_simplex(head_iso, sum_target_rest, 0.0, 1.0)
        # Re-apply isotonic to clean tiny violations then re-project to restore mass exactly
        head_proj = _isotonic_non_decreasing(head_proj)
        head_proj = np.clip(head_proj, 0.0, 1.0)
        head_proj = _project_boxed_simplex(head_proj, sum_target_rest, 0.0, 1.0)
        x[:-1] = head_proj
        x[-1] = last_fixed
        return x

    def anneal_optimize_half(x: np.ndarray) -> np.ndarray:
        """Run the softmax annealing optimization with Adam and projections."""
        m = len(x)
        last_fixed = 0.5
        x = project_half(x, last_fixed=last_fixed)
        # Adam optimizer state
        opt_state = {"beta1": 0.9, "beta2": 0.999, "eps": 1e-8, "t": 0, "m": np.zeros_like(x), "v": np.zeros_like(x)}
        best_x = x.copy()
        best_ub = _compute_upper_bound_from_half(best_x)
        for tau in tau_schedule:
            lr = base_lr / math.sqrt(tau)
            for _ in range(iters_per_tau):
                y = _finalize_full_sequence(x)
                _, grad_y = _softmax_objective_and_grad_y(y, tau=tau)
                grad_x = _map_grad_y_to_half(grad_y, m=m)
                x = _adam_update(x, grad_x, opt_state, lr=lr)
                x = project_half(x, last_fixed=last_fixed)
                ub = _compute_upper_bound_from_half(x)
                if ub < best_ub:
                    best_ub = ub
                    best_x = x.copy()
        return best_x

    def _armijo_step(x: np.ndarray, grad_x: np.ndarray, lr_init: float, eval_fn, project_fn, max_backtracks: int = 8):
        """Armijo-like backtracking on the hard objective. Returns improved x and ub."""
        base_ub = eval_fn(x)
        step = lr_init
        best_x_local = x
        best_ub_local = base_ub
        for _ in range(max_backtracks):
            cand = x - step * grad_x
            cand = project_fn(cand)
            ub = eval_fn(cand)
            if ub < best_ub_local - 1e-9:
                best_x_local = cand
                best_ub_local = ub
                break
            step *= 0.5
        return best_x_local, best_ub_local

    def topk_and_active_polish(x: np.ndarray) -> np.ndarray:
        """Top-k and active-set polishing followed by single-shift shaving."""
        m = len(x)
        last_fixed = 0.5
        x = project_half(x, last_fixed=last_fixed)
        best_x = x.copy()
        best_ub = _compute_upper_bound_from_half(best_x)
        n = 2 * m - 1

        def eval_fn(cur_x):
            return _compute_upper_bound_from_half(cur_x)

        def project_fn(cur_x):
            return project_half(cur_x, last_fixed=last_fixed)

        # Top-k polishing with Armijo
        for t in range(topk_steps):
            y = _finalize_full_sequence(x)
            F_vals = _compute_overlap_values(y)
            order = np.argsort(F_vals)[::-1]
            top_indices = order[:topk_k]
            top_shifts = [int(idx - (n - 1)) for idx in top_indices]
            grads_y = np.zeros_like(y)
            for s in top_shifts:
                grads_y += _single_shift_grad_y(y, s)
            grads_y /= float(len(top_shifts))
            grad_x = _map_grad_y_to_half(grads_y, m=m)
            # Armijo step
            x_new, ub_new = _armijo_step(x, grad_x, topk_lr_init, eval_fn, project_fn, max_backtracks=8)
            x = x_new
            if ub_new < best_ub:
                best_ub = ub_new
                best_x = x.copy()
            # Recompute top-k frequently as peaks move
            if (t + 1) % topk_recompute_every == 0:
                continue

        # Active-set polishing: include all shifts within a small relative tol of max
        for t in range(active_steps):
            y = _finalize_full_sequence(x)
            F_vals = _compute_overlap_values(y)
            F_max = float(np.max(F_vals))
            # Active set includes lags with F within (1 - tol)*F_max
            active_mask = F_vals >= (1.0 - active_tol_ratio) * F_max
            active_indices = np.where(active_mask)[0]
            if len(active_indices) == 0:
                break
            grads_y = np.zeros_like(y)
            for idx in active_indices:
                s = int(idx - (n - 1))
                grads_y += _single_shift_grad_y(y, s)
            grads_y /= float(len(active_indices))
            grad_x = _map_grad_y_to_half(grads_y, m=m)
            x_new, ub_new = _armijo_step(x, grad_x, active_lr_init, eval_fn, project_fn, max_backtracks=10)
            x = x_new
            if ub_new < best_ub:
                best_ub = ub_new
                best_x = x.copy()
            if (t + 1) % active_recompute_every == 0:
                continue

        # Single-shift final shaving
        x = best_x.copy()
        for t in range(single_steps):
            y = _finalize_full_sequence(x)
            F_vals = _compute_overlap_values(y)
            k_star = int(np.argmax(F_vals))
            s_star = k_star - (n - 1)
            grad_y = _single_shift_grad_y(y, s_star)
            grad_x = _map_grad_y_to_half(grad_y, m=m)
            x_new, ub_new = _armijo_step(x, grad_x, single_lr_init, eval_fn, project_fn, max_backtracks=10)
            x = x_new
            if ub_new < best_ub:
                best_ub = ub_new
                best_x = x.copy()
            if (t + 1) % single_recompute_every == 0:
                continue

        return best_x

    # Multi-start initializations at the coarsest resolution
    m0 = schedule_ms[0]
    last_fixed = 0.5
    inits = []

    # 1) Uniform at 0.5
    x0 = np.full(m0, 0.5, dtype=np.float64)
    inits.append(x0)

    # 2) Tapered toward center bump, then project
    idx = np.arange(m0, dtype=np.float64)
    center = m0 - 1
    taper = np.exp(-((idx - center) ** 2) / (0.16 * m0) ** 2)
    x1 = 0.5 + 0.15 * (taper - taper.mean())
    inits.append(project_half(x1, last_fixed=last_fixed))

    # 3) Linear ramp increasing to 0.5 with small edge bias
    head = np.linspace(0.05, 0.5, num=m0 - 1)
    x2 = np.concatenate([head, np.array([0.5])])
    inits.append(project_half(x2, last_fixed=last_fixed))

    # 4) Small random perturbation around 0.5
    x3 = 0.5 + 0.04 * rng.standard_normal(m0)
    inits.append(project_half(x3, last_fixed=last_fixed))

    # 5) Cosine bump towards center
    t = np.linspace(0.0, 1.0, m0)
    x4 = 0.5 - 0.12 * np.cos(np.pi * t)
    inits.append(project_half(x4, last_fixed=last_fixed))

    # 6) Sine ramp
    x5 = 0.5 - 0.1 * np.sin(0.5 * np.pi * t)
    inits.append(project_half(x5, last_fixed=last_fixed))

    best_half = None
    best_bound = float("inf")

    # For each init, run coarse-to-fine schedule
    for init in inits:
        x = project_half(init.copy(), last_fixed=last_fixed)
        # Loop over resolution schedule
        for i_stage, m in enumerate(schedule_ms):
            if len(x) != m:
                # Resample and project
                x = _resample_half_linear(x, m_new=m)
                x = project_half(x, last_fixed=last_fixed)
            # Annealed optimization at this resolution
            x = anneal_optimize_half(x)
        # Polishing at finest resolution
        x = topk_and_active_polish(x)
        # Track the best candidate by true upper bound
        ub = _compute_upper_bound_from_half(x)
        if ub < best_bound:
            best_bound = ub
            best_half = x.copy()

    # Final guard: exact projection to ensure feasibility
    best_half = project_half(best_half, last_fixed=last_fixed)
    return best_half
# EVOLVE_END


if __name__ == "__main__":
    # Convert ndarray to list for JSON serialization
    half = generate_erdos_data()
    print(json.dumps({"half_sequence": half.tolist()}))
```

$$
\sup_{x \in [-2,2]} \int_{-1}^1 f(t) g(x+t)\ dt\geq C_5
$$

$$
0.379005 \leq C_5 \leq 0.380927
$$

```python
#!/usr/bin/env python3
"""Optimization-based sequence generator for Erdős' minimum overlap problem.

This implements a structured, projected optimization on a symmetric discrete step
function h over [0, 2], represented by a half-sequence that is mirrored as in the
usage example. The objective minimizes a smoothed maximum (softmax) of the discrete
overlap sums sum_i h_i (1 - h_{i+s}) across all integer shifts s, which corresponds
to the compute_upper_bound procedure used externally.

We enforce feasibility at all times:
- Values are clamped to [0, 1]
- The last coefficient of the half-sequence is fixed to 0.5 so that after mirroring
  the final sequence has exact mass n/2
- The sum of the half-sequence equals m/2 via a water-filling (boxed simplex) projection.
- NEW: Enforce unimodality (nondecreasing) on the first m-1 entries (half-sequence
  excluding the center) via isotonic regression (Pool Adjacent Violators) as a projection
  step. This keeps the search inside the symmetric unimodal family which empirically
  lowers the maximum overlap.

Two improvements added:
1) Unimodality projection on the half-sequence after each gradient step: After the Adam
   update and capped-simplex projection on the first m-1 coordinates (with x[-1] fixed
   at 0.5), enforce nondecreasing monotonicity on x[:-1] using isotonic regression, then
   re-project onto the capped simplex to restore the sum constraint and clamp to [0,1].
2) Argmax polishing: After the annealed softmax stages, identify the active shift s*
   that maximizes the discrete overlap on the finalized full sequence. Run a brief
   projected subgradient descent minimizing F_{s*}(h) with the exact gradient
   ∂F_{s*}/∂y_j = (1−y_{j+s*}) − y_{j−s*} mapped back to the half-sequence, enforcing the
   unimodal capped-simplex constraints after each step. Recompute s* periodically and
   switch if the argmax changes.

These adjustments empirically reduce the resulting upper bound below the previous
construction threshold.
"""

import json
import math
from typing import Tuple

import numpy as np


# EVOLVE_START
def _finalize_full_sequence(half: np.ndarray) -> np.ndarray:
    """Construct the final symmetric sequence from a half-sequence.

    The external code forms final_sequence = concatenate(half[:-1], half[::-1]).
    We use the same construction for objective evaluation during optimization.
    """
    return np.concatenate((half[:-1], half[::-1]))


def _compute_overlap_values(y: np.ndarray) -> np.ndarray:
    """Compute discrete overlap sums F[s] = sum_i y_i (1 - y_{i+s}) for all integer
    shifts s in the range [-(n-1), ..., +(n-1)].

    This matches the values produced by np.correlate(y, 1 - y, mode='full'), but is
    implemented explicitly for ease of gradient handling and correctness of indexing.

    Returns:
        F: np.ndarray of length 2n - 1 with F[n-1 + s] corresponding to shift s.
    """
    n = len(y)
    F = np.zeros(2 * n - 1, dtype=np.float64)
    # Positive shifts s >= 0
    for s in range(0, n):
        # Overlap indices: i in [0, n - 1 - s]
        a = y[: n - s]
        b = y[s:]
        F[n - 1 + s] = np.sum(a) - np.sum(a * b)
    # Negative shifts s = -t, t in [1, n-1]
    for t in range(1, n):
        # Overlap indices: i in [t, n-1]
        a = y[t:]
        b = y[: n - t]
        F[n - 1 - t] = np.sum(a) - np.sum(a * b)
    return F


def _softmax_objective_and_grad_y(y: np.ndarray, tau: float) -> Tuple[float, np.ndarray]:
    """Compute softmax-smoothed objective and its gradient with respect to y.

    Objective approximates max_s F[s] / (n * 2) with:
        obj_tau = (1/tau) * log sum_s exp(tau * F[s]) / (n * 2)

    Gradient wrt y is the softmax-weighted average of grad F[s], scaled by 1/(n*2).

    Returns:
        obj: scalar objective value
        grad_y: gradient vector of length len(y)
    """
    n = len(y)
    F = _compute_overlap_values(y)
    # Numerically stable softmax weights
    z = tau * F
    z_max = np.max(z)
    w = np.exp(z - z_max)
    w_sum = np.sum(w)
    w /= w_sum

    # Objective value
    obj = (math.log(w_sum) + z_max) / tau / (n * 2.0)

    # Gradient wrt y by vectorized slice accumulation
    grad_y = np.zeros_like(y, dtype=np.float64)
    # Positive shifts s >= 0 -> index k = n - 1 + s
    for s in range(0, n):
        k = n - 1 + s
        wk = w[k]
        # Contributions on positions 0..n-s-1: +1 - y[s:]
        grad_y[: n - s] += wk * (1.0 - y[s:])
        # Contributions on positions s..n-1: - y[:n-s]
        grad_y[s:] -= wk * y[: n - s]

    # Negative shifts s = -t, t in [1, n-1] -> index k = n - 1 - t
    for t in range(1, n):
        k = n - 1 - t
        wk = w[k]
        # Contributions on positions t..n-1: +1 - y[:n-t]
        grad_y[t:] += wk * (1.0 - y[: n - t])
        # Contributions on positions 0..n-t-1: - y[t:]
        grad_y[: n - t] -= wk * y[t:]

    # Scale gradient due to final normalization by 1 / (n * 2)
    grad_y /= (n * 2.0)
    return obj, grad_y


def _map_grad_y_to_half(grad_y: np.ndarray, m: int) -> np.ndarray:
    """Map gradient from full sequence y to half sequence x.

    Final y is built from half x as: y = concat(x[:-1], x[::-1])
    So:
      - For j in [0, m-2], x_j appears twice in y at indices j and (2m-2 - j).
      - For j = m-1, x_{m-1} appears once in y at index m-1.

    Sum the corresponding gradient contributions.
    """
    n = 2 * m - 1
    assert len(grad_y) == n
    grad_x = np.zeros(m, dtype=np.float64)
    # j in [0..m-2]
    for j in range(m - 1):
        grad_x[j] = grad_y[j] + grad_y[n - 1 - j]
    # center element j = m - 1
    grad_x[m - 1] = grad_y[m - 1]
    return grad_x


def _project_boxed_simplex(v: np.ndarray, target_sum: float, lower: float = 0.0, upper: float = 1.0) -> np.ndarray:
    """Project vector v onto the capped simplex:
        { x in [lower, upper]^d : sum(x) = target_sum }

    Uses bisection on the Lagrange multiplier lambda for x = clip(v - lambda, lower, upper).
    """
    d = len(v)
    # Handle trivial bounds
    lower = float(lower)
    upper = float(upper)
    # If target is outside feasible bounds, clamp to closest feasible
    min_sum = d * lower
    max_sum = d * upper
    if target_sum <= min_sum:
        return np.full(d, lower, dtype=np.float64)
    if target_sum >= max_sum:
        return np.full(d, upper, dtype=np.float64)

    # Bracket lambda: moving lambda smaller increases x (toward upper), larger decreases x (toward lower).
    lam_lo = np.min(v) - upper  # makes most coordinates at upper bound
    lam_hi = np.max(v) - lower  # makes most coordinates at lower bound

    # Bisection
    for _ in range(60):  # sufficient precision for double
        lam_mid = 0.5 * (lam_lo + lam_hi)
        x = v - lam_mid
        # Clip to [lower, upper]
        x = np.clip(x, lower, upper)
        s = x.sum()
        if s > target_sum:
            # Need to reduce sum -> increase lambda
            lam_lo = lam_mid
        else:
            lam_hi = lam_mid
    lam = 0.5 * (lam_lo + lam_hi)
    x = np.clip(v - lam, lower, upper)
    return x


def _adam_update(x: np.ndarray, grad: np.ndarray, opt_state: dict, lr: float) -> np.ndarray:
    """One Adam optimizer update step."""
    beta1 = opt_state.get("beta1", 0.9)
    beta2 = opt_state.get("beta2", 0.999)
    eps = opt_state.get("eps", 1e-8)
    t = opt_state.get("t", 0) + 1

    m = opt_state.get("m", np.zeros_like(x))
    v = opt_state.get("v", np.zeros_like(x))

    m = beta1 * m + (1.0 - beta1) * grad
    v = beta2 * v + (1.0 - beta2) * (grad * grad)

    m_hat = m / (1.0 - beta1 ** t)
    v_hat = v / (1.0 - beta2 ** t)

    x_new = x - lr * m_hat / (np.sqrt(v_hat) + eps)

    opt_state["m"] = m
    opt_state["v"] = v
    opt_state["t"] = t
    return x_new


def _compute_upper_bound_from_half(half: np.ndarray) -> float:
    """Compute the external upper bound for a given half-sequence using the
    same procedure as in the provided compute_upper_bound function."""
    y = _finalize_full_sequence(half)
    conv_vals = np.correlate(y, 1.0 - y, mode="full")
    return np.max(conv_vals) / len(y) * 2.0


def _isotonic_non_decreasing(y: np.ndarray) -> np.ndarray:
    """Pool Adjacent Violators algorithm (PAV) for L2 isotonic regression
    enforcing nondecreasing order.

    Returns the projection of y onto the set {x: x[0] <= x[1] <= ... } minimizing L2.
    """
    n = len(y)
    # Initialize blocks with unit weights
    # Each block stores (weight_sum, weighted_value_sum)
    blocks_w = []
    blocks_wv = []
    for i in range(n):
        wi = 1.0
        wvi = wi * y[i]
        blocks_w.append(wi)
        blocks_wv.append(wvi)
        # Merge adjacent blocks if monotonicity violated
        while len(blocks_w) >= 2:
            w1 = blocks_w[-2]
            w2 = blocks_w[-1]
            a1 = blocks_wv[-2] / w1
            a2 = blocks_wv[-1] / w2
            if a1 > a2:
                # Merge last two blocks
                blocks_w[-2] = w1 + w2
                blocks_wv[-2] = blocks_wv[-2] + blocks_wv[-1]
                blocks_w.pop()
                blocks_wv.pop()
            else:
                break
    # Expand blocks back to a full vector with fitted block means
    res = np.empty(n, dtype=np.float64)
    idx = 0
    for bw, bwv in zip(blocks_w, blocks_wv):
        val = bwv / bw
        cnt = int(round(bw))  # bw should be an integer since all weights were 1.0
        # Safety: ensure at least 1
        cnt = max(1, cnt)
        res[idx : idx + cnt] = val
        idx += cnt
    return res


def _single_shift_grad_y(y: np.ndarray, s: int) -> np.ndarray:
    """Compute exact gradient of F[s] = sum_i y_i (1 - y_{i+s}) with respect to y.

    Handles both positive and negative shifts s.
    """
    n = len(y)
    grad = np.zeros_like(y)
    if s >= 0:
        if s < n:
            grad[: n - s] += (1.0 - y[s:])
            grad[s:] -= y[: n - s]
    else:
        t = -s
        if t < n:
            grad[t:] += (1.0 - y[: n - t])
            grad[: n - t] -= y[t:]
    return grad


def _resample_half_linear(x_old: np.ndarray, m_new: int) -> np.ndarray:
    """Resample a half sequence to a new length using linear interpolation.

    Ensures the last element (center) remains exactly 0.5, but mass will be corrected
    by projection afterwards.
    """
    m_old = len(x_old)
    if m_new == m_old:
        return x_old.copy()
    # Parameterize both old and new grids on [0, 1]
    xp_old = np.linspace(0.0, 1.0, num=m_old)
    xp_new = np.linspace(0.0, 1.0, num=m_new)
    x_new = np.interp(xp_new, xp_old, x_old)
    # Ensure strict feasibility constraints will be re-enforced later
    x_new[-1] = 0.5
    x_new = np.clip(x_new, 0.0, 1.0)
    return x_new


def generate_erdos_data() -> np.ndarray:
    """Generates a sequence of coefficients for Erdős' minimum overlap problem.

    CF-TK+PS-AS: Coarse-to-Fine annealed optimization with unimodal projection, and
    Top-K + Active-set polishing with Armijo backtracking.

    - Half-sequence parameterization with pinned center 0.5.
    - Capped-simplex projection for exact mass and box constraints.
    - Isotonic projection for unimodality on x[:-1].
    - Smoothed min-max optimization with temperature continuation and Adam.
    - Coarse-to-fine resolution schedule with symmetric linear upsampling.
    - Top-k polishing and active-set polishing on the exact verifier objective.

    Returns:
        np.ndarray: Best found half-sequence (values in [0,1], sum = m/2, last=0.5).
    """
    # Coarse-to-fine schedule of half lengths (final full n = 2*m − 1)
    # Slightly higher final resolution improves polishing stability.
    schedule_ms = [96, 144, 192]

    # Temperature (softmax) schedule, from smooth to sharp
    tau_schedule = [1.0, 3.0, 10.0, 30.0, 100.0, 300.0, 900.0]
    iters_per_tau = 50
    base_lr = 0.05  # Adam base step; scaled by sqrt(tau)

    # Polishing configuration (Top-K and Active-Set with Armijo backtracking)
    topk_k = 3
    topk_steps = 40
    topk_lr_init = 0.06
    topk_armijo_beta = 0.5
    topk_recompute_every = 2

    # Active set polishing (shifts within tol of current max)
    active_tol_ratio = 1e-3  # within 0.1% of max gets included
    active_steps = 35
    active_lr_init = 0.05
    active_armijo_beta = 0.5
    active_recompute_every = 1

    # Single-shift final shaving
    single_steps = 30
    single_lr_init = 0.05
    single_armijo_beta = 0.5
    single_recompute_every = 1

    # RNG for reproducible inits
    rng = np.random.default_rng(12345)

    def project_half(x: np.ndarray, last_fixed: float) -> np.ndarray:
        """Project onto feasible set: [0,1]^m, x[-1]=last_fixed, sum(x)=m/2, enforce unimodality."""
        m = len(x)
        sum_target_half = m / 2.0
        sum_target_rest = sum_target_half - last_fixed
        x = np.asarray(x, dtype=np.float64)
        x = np.clip(x, 0.0, 1.0)
        x[-1] = last_fixed
        # Isotonic regression on head (monotone nondecreasing)
        head = x[:-1]
        head_iso = _isotonic_non_decreasing(head)
        head_iso = np.clip(head_iso, 0.0, 1.0)
        # Project to capped simplex with exact sum for head
        head_proj = _project_boxed_simplex(head_iso, sum_target_rest, 0.0, 1.0)
        # Re-apply isotonic to clean tiny violations then re-project to restore mass exactly
        head_proj = _isotonic_non_decreasing(head_proj)
        head_proj = np.clip(head_proj, 0.0, 1.0)
        head_proj = _project_boxed_simplex(head_proj, sum_target_rest, 0.0, 1.0)
        x[:-1] = head_proj
        x[-1] = last_fixed
        return x

    def anneal_optimize_half(x: np.ndarray) -> np.ndarray:
        """Run the softmax annealing optimization with Adam and projections."""
        m = len(x)
        last_fixed = 0.5
        x = project_half(x, last_fixed=last_fixed)
        # Adam optimizer state
        opt_state = {"beta1": 0.9, "beta2": 0.999, "eps": 1e-8, "t": 0, "m": np.zeros_like(x), "v": np.zeros_like(x)}
        best_x = x.copy()
        best_ub = _compute_upper_bound_from_half(best_x)
        for tau in tau_schedule:
            lr = base_lr / math.sqrt(tau)
            for _ in range(iters_per_tau):
                y = _finalize_full_sequence(x)
                _, grad_y = _softmax_objective_and_grad_y(y, tau=tau)
                grad_x = _map_grad_y_to_half(grad_y, m=m)
                x = _adam_update(x, grad_x, opt_state, lr=lr)
                x = project_half(x, last_fixed=last_fixed)
                ub = _compute_upper_bound_from_half(x)
                if ub < best_ub:
                    best_ub = ub
                    best_x = x.copy()
        return best_x

    def _armijo_step(x: np.ndarray, grad_x: np.ndarray, lr_init: float, eval_fn, project_fn, max_backtracks: int = 8):
        """Armijo-like backtracking on the hard objective. Returns improved x and ub."""
        base_ub = eval_fn(x)
        step = lr_init
        best_x_local = x
        best_ub_local = base_ub
        for _ in range(max_backtracks):
            cand = x - step * grad_x
            cand = project_fn(cand)
            ub = eval_fn(cand)
            if ub < best_ub_local - 1e-9:
                best_x_local = cand
                best_ub_local = ub
                break
            step *= 0.5
        return best_x_local, best_ub_local

    def topk_and_active_polish(x: np.ndarray) -> np.ndarray:
        """Top-k and active-set polishing followed by single-shift shaving."""
        m = len(x)
        last_fixed = 0.5
        x = project_half(x, last_fixed=last_fixed)
        best_x = x.copy()
        best_ub = _compute_upper_bound_from_half(best_x)
        n = 2 * m - 1

        def eval_fn(cur_x):
            return _compute_upper_bound_from_half(cur_x)

        def project_fn(cur_x):
            return project_half(cur_x, last_fixed=last_fixed)

        # Top-k polishing with Armijo
        for t in range(topk_steps):
            y = _finalize_full_sequence(x)
            F_vals = _compute_overlap_values(y)
            order = np.argsort(F_vals)[::-1]
            top_indices = order[:topk_k]
            top_shifts = [int(idx - (n - 1)) for idx in top_indices]
            grads_y = np.zeros_like(y)
            for s in top_shifts:
                grads_y += _single_shift_grad_y(y, s)
            grads_y /= float(len(top_shifts))
            grad_x = _map_grad_y_to_half(grads_y, m=m)
            # Armijo step
            x_new, ub_new = _armijo_step(x, grad_x, topk_lr_init, eval_fn, project_fn, max_backtracks=8)
            x = x_new
            if ub_new < best_ub:
                best_ub = ub_new
                best_x = x.copy()
            # Recompute top-k frequently as peaks move
            if (t + 1) % topk_recompute_every == 0:
                continue

        # Active-set polishing: include all shifts within a small relative tol of max
        for t in range(active_steps):
            y = _finalize_full_sequence(x)
            F_vals = _compute_overlap_values(y)
            F_max = float(np.max(F_vals))
            # Active set includes lags with F within (1 - tol)*F_max
            active_mask = F_vals >= (1.0 - active_tol_ratio) * F_max
            active_indices = np.where(active_mask)[0]
            if len(active_indices) == 0:
                break
            grads_y = np.zeros_like(y)
            for idx in active_indices:
                s = int(idx - (n - 1))
                grads_y += _single_shift_grad_y(y, s)
            grads_y /= float(len(active_indices))
            grad_x = _map_grad_y_to_half(grads_y, m=m)
            x_new, ub_new = _armijo_step(x, grad_x, active_lr_init, eval_fn, project_fn, max_backtracks=10)
            x = x_new
            if ub_new < best_ub:
                best_ub = ub_new
                best_x = x.copy()
            if (t + 1) % active_recompute_every == 0:
                continue

        # Single-shift final shaving
        x = best_x.copy()
        for t in range(single_steps):
            y = _finalize_full_sequence(x)
            F_vals = _compute_overlap_values(y)
            k_star = int(np.argmax(F_vals))
            s_star = k_star - (n - 1)
            grad_y = _single_shift_grad_y(y, s_star)
            grad_x = _map_grad_y_to_half(grad_y, m=m)
            x_new, ub_new = _armijo_step(x, grad_x, single_lr_init, eval_fn, project_fn, max_backtracks=10)
            x = x_new
            if ub_new < best_ub:
                best_ub = ub_new
                best_x = x.copy()
            if (t + 1) % single_recompute_every == 0:
                continue

        return best_x

    # Multi-start initializations at the coarsest resolution
    m0 = schedule_ms[0]
    last_fixed = 0.5
    inits = []

    # 1) Uniform at 0.5
    x0 = np.full(m0, 0.5, dtype=np.float64)
    inits.append(x0)

    # 2) Tapered toward center bump, then project
    idx = np.arange(m0, dtype=np.float64)
    center = m0 - 1
    taper = np.exp(-((idx - center) ** 2) / (0.16 * m0) ** 2)
    x1 = 0.5 + 0.15 * (taper - taper.mean())
    inits.append(project_half(x1, last_fixed=last_fixed))

    # 3) Linear ramp increasing to 0.5 with small edge bias
    head = np.linspace(0.05, 0.5, num=m0 - 1)
    x2 = np.concatenate([head, np.array([0.5])])
    inits.append(project_half(x2, last_fixed=last_fixed))

    # 4) Small random perturbation around 0.5
    x3 = 0.5 + 0.04 * rng.standard_normal(m0)
    inits.append(project_half(x3, last_fixed=last_fixed))

    # 5) Cosine bump towards center
    t = np.linspace(0.0, 1.0, m0)
    x4 = 0.5 - 0.12 * np.cos(np.pi * t)
    inits.append(project_half(x4, last_fixed=last_fixed))

    # 6) Sine ramp
    x5 = 0.5 - 0.1 * np.sin(0.5 * np.pi * t)
    inits.append(project_half(x5, last_fixed=last_fixed))

    best_half = None
    best_bound = float("inf")

    # For each init, run coarse-to-fine schedule
    for init in inits:
        x = project_half(init.copy(), last_fixed=last_fixed)
        # Loop over resolution schedule
        for i_stage, m in enumerate(schedule_ms):
            if len(x) != m:
                # Resample and project
                x = _resample_half_linear(x, m_new=m)
                x = project_half(x, last_fixed=last_fixed)
            # Annealed optimization at this resolution
            x = anneal_optimize_half(x)
        # Polishing at finest resolution
        x = topk_and_active_polish(x)
        # Track the best candidate by true upper bound
        ub = _compute_upper_bound_from_half(x)
        if ub < best_bound:
            best_bound = ub
            best_half = x.copy()

    # Final guard: exact projection to ensure feasibility
    best_half = project_half(best_half, last_fixed=last_fixed)
    return best_half
# EVOLVE_END


if __name__ == "__main__":
    # Convert ndarray to list for JSON serialization
    half = generate_erdos_data()
    print(json.dumps({"half_sequence": half.tolist()}))
```

```text
\sup_{x \in [-2,2]} \int_{-1}^1 f(t) g(x+t)\ dt\geq C_5
```

```text
0.379005 \leq C_5 \leq 0.380927
```
