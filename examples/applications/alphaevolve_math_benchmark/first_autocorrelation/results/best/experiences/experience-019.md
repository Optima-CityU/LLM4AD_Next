Acceptance‑tested plateau‑focused suppression with inverse‑pressure tail and Top‑R blending; supported by implementation and run metrics.

- TPM‑IPT+ works on logits with a = softmax(s), ensuring a ≥ 0 and sum(a) = 1 at every step, and evaluates the exact objective using FFT primitives.
- TPM‑IPT+: The plateau‑union Top‑K surrogate mixes gap‑adaptive Top‑R blending and an inverse‑pressure tail whose weight α increases with plateau width and decreases with the top‑2 gap.
- solve.py (TPM‑IPT+ implementation): The implementation integrates dual‑peak microsteps that simultaneously suppress top‑2/3 near‑tied maxima and a p‑norm polish targeting the max norm.
- Metrics for Generation 6: Validity was 1.0, upper_bound was 1.5128288813801904, target_ratio was 0.9950233093293925, and eval_time was 543.6964695809875 seconds.

```python
#!/usr/bin/env python3
"""Search for a 600-step nonnegative sequence that minimizes the autocorrelation-based upper bound.

Implements TPM‑IPT+: a plateau‑union Top‑K surrogate with Top‑R blend and inverse‑pressure tail,
optimized on the L1‑simplex via logits, with FFT‑accelerated exact evaluation. It interleaves
annealed surrogate descent, pressure microsteps (plateau-focused and dual-peak), and acceptance‑tested
non‑smooth refinements (hard‑max polish, cutting‑plane average-of-tops, pair‑shaving transport, and
p‑norm polish). Seeds are diversified and strengthened via a width‑swept arch selection with accepted
peak‑shaving microsteps, plus a multi‑resolution bootstrap. All hard moves and symmetry mixes are
strictly acceptance‑tested against the exact objective, ensuring monotone improvements.

This evolution integrates:
- Dual-peak microsteps that simultaneously suppress top-2/3 near‑tied maxima, enhancing stability.
- p‑norm polish that targets high peaks via a smooth surrogate close to the max norm.
- Slightly expanded multi-resolution bootstrap for stronger seeds with tight time allocation.
- Tuned schedules to sharpen focus without destabilization.
"""

import json
import math
import time
from typing import Tuple, List

import numpy as np


# EVOLVE_START
def softmax(x: np.ndarray) -> np.ndarray:
    """Numerically stable softmax."""
    m = np.max(x)
    z = x - m
    ex = np.exp(np.clip(z, -60.0, 60.0))
    s = ex.sum()
    if s == 0.0 or not np.isfinite(s):
        ex = np.exp(np.clip(z, -50.0, 50.0))
        s = ex.sum()
        if s == 0.0 or not np.isfinite(s):
            return np.full_like(x, 1.0 / len(x))
    return ex / s


def next_pow2(n: int) -> int:
    """Next power of two >= n."""
    return 1 << (n - 1).bit_length()


def conv1d_fft(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Linear convolution of real 1D arrays using FFT (length len(x)+len(y)-1)."""
    n = len(x)
    m = len(y)
    L = n + m - 1
    nfft = next_pow2(L)
    X = np.fft.rfft(x, nfft)
    Y = np.fft.rfft(y, nfft)
    Z = X * Y
    z = np.fft.irfft(Z, nfft)
    return z[:L]


def self_convolution(a: np.ndarray) -> np.ndarray:
    """Return b = conv(a, a) as linear convolution length 2n-1."""
    return conv1d_fft(a, a)


def objective_from_b(b: np.ndarray, n: int) -> float:
    """F(a) = 2 * n * max(conv(a, a)), valid when sum(a) = 1."""
    return float(2.0 * n * float(np.max(b)))


def exact_objective(a: np.ndarray) -> Tuple[float, np.ndarray]:
    """Compute exact objective and self-convolution."""
    b = self_convolution(a)
    F = objective_from_b(b, len(a))
    return F, b


def grad_corr_wrt_a(w: np.ndarray, a: np.ndarray) -> np.ndarray:
    """Compute gradient wrt a of ⟨w, conv(a, a)⟩ which equals 2 * corr(w, a).
    Implement via convolution with reversed a:
      corr(w, a)[j] = sum_k w[k] * a[k - j] = (w * reverse(a))[j + (n - 1)]
    """
    n = len(a)
    ar = a[::-1]
    t = conv1d_fft(w, ar)
    start = n - 1
    corr_vals = t[start : start + n]
    return 2.0 * corr_vals


def tv2_and_grad(a: np.ndarray) -> Tuple[float, np.ndarray]:
    """Second-difference squared (TV^2-like) penalty and its gradient."""
    n = len(a)
    sdd = np.zeros_like(a)
    if n >= 2:
        sdd[0] = a[0] - a[1]
        sdd[-1] = a[-1] - a[-2]
    if n >= 3:
        sdd[1:-1] = a[:-2] - 2.0 * a[1:-1] + a[2:]

    g = np.zeros_like(a)
    if n >= 2:
        g[0] += sdd[0]
        g[1] += -sdd[0]
        g[-1] += sdd[-1]
        g[-2] += -sdd[-1]
    if n >= 3:
        g[:-2] += sdd[1:-1]
        g[1:-1] += -2.0 * sdd[1:-1]
        g[2:] += sdd[1:-1]

    loss = float(np.dot(sdd, sdd))
    grad = 2.0 * g
    return loss, grad


class AdamAMSGrad:
    """Adam optimizer with AMSGrad variant for stability."""
    def __init__(self, dim: int, lr: float = 0.05, beta1: float = 0.9, beta2: float = 0.999, eps: float = 1e-8):
        self.m = np.zeros(dim, dtype=np.float64)
        self.v = np.zeros(dim, dtype=np.float64)
        self.vhat = np.zeros(dim, dtype=np.float64)
        self.beta1 = beta1
        self.beta2 = beta2
        self.eps = eps
        self.lr = lr
        self.t = 0

    def step(self, grad: np.ndarray) -> np.ndarray:
        self.t += 1
        self.m = self.beta1 * self.m + (1.0 - self.beta1) * grad
        self.v = self.beta2 * self.v + (1.0 - self.beta2) * (grad * grad)
        self.vhat = np.maximum(self.vhat, self.v)
        m_hat = self.m / (1.0 - self.beta1 ** self.t)
        v_hat = self.vhat / (1.0 - self.beta2 ** self.t)
        return -self.lr * m_hat / (np.sqrt(v_hat) + self.eps)

    def set_lr(self, lr: float):
        self.lr = lr


def build_seeds(n: int, rng: np.random.Generator) -> List[np.ndarray]:
    """Generate a set of initial seeds on the simplex (windows/arches/gaussians/perturbations)."""
    x = np.linspace(-0.25, 0.25, n)
    seeds = []

    # Arch family with width scaling (parabolic arch clipped to nonnegative)
    width_scales = [0.68, 0.74, 0.80, 0.88, 0.96, 1.04, 1.12, 1.20, 1.30]
    for s in width_scales:
        xs = x / s
        arch = 1.0 + 4.0 * np.abs(xs) - 16.0 * xs * xs
        arch = np.maximum(arch, 0.0)
        arch = 0.5 * (arch + arch[::-1])  # symmetrize
        if arch.sum() > 0:
            arch /= arch.sum()
            seeds.append(arch)

    # Uniform
    seeds.append(np.full(n, 1.0 / n))

    # Raised cosine (Hann window shape normalized)
    t = np.linspace(-1.0, 1.0, n)
    rc = 0.5 * (1.0 + np.cos(np.pi * t))
    rc /= rc.sum()
    seeds.append(rc)

    # Triangular
    tri = 1.0 - np.abs(np.linspace(-1.0, 1.0, n))
    tri = np.maximum(tri, 0.0)
    tri /= tri.sum()
    seeds.append(tri)

    # Gaussian variants
    for sigma in [0.25, 0.30, 0.35, 0.42]:
        g = np.exp(-0.5 * (t / sigma) ** 2)
        g /= g.sum()
        seeds.append(g)

    # Kaiser and Blackman windows (smooth)
    try:
        kw = np.kaiser(n, beta=7.0)
        kw = np.maximum(kw, 0.0)
        kw /= kw.sum()
        seeds.append(kw)
    except Exception:
        pass
    bw = 0.42 - 0.5 * np.cos(2 * np.pi * (np.arange(n) / (n - 1))) + 0.08 * np.cos(4 * np.pi * (np.arange(n) / (n - 1)))
    bw = np.maximum(bw, 0.0)
    bw /= bw.sum()
    seeds.append(bw)

    # Tukey window variants
    for alpha in [0.3, 0.5, 0.7]:
        m = np.arange(n)
        frac = m / (n - 1)
        tuk = np.ones(n, dtype=np.float64)
        left = frac < alpha / 2
        right = frac > 1 - alpha / 2
        tuk[left] = 0.5 * (1 + np.cos(np.pi * (2 * frac[left] / alpha - 1)))
        tuk[right] = 0.5 * (1 + np.cos(np.pi * (2 * (frac[right] - 1) / alpha + 1)))
        tuk = np.maximum(tuk, 0.0)
        tuk /= tuk.sum()
        seeds.append(tuk)

    # Chebyshev-like smooth window (approx via kaiser with tuned beta)
    try:
        ch = np.kaiser(n, beta=8.5)
        ch = np.maximum(ch, 0.0)
        ch /= ch.sum()
        seeds.append(ch)
    except Exception:
        pass

    # Slightly perturbed variants of a good arch (mild asymmetry exploration)
    base = seeds[3] if len(seeds) > 3 else seeds[0]
    for _ in range(12):
        noise = rng.normal(0.0, 0.02, size=n)
        pert = base * (1.0 + noise)
        w = rng.uniform(0.6, 0.85)
        pert = w * pert + (1.0 - w) * pert[::-1]
        pert = np.maximum(pert, 0.0)
        if pert.sum() == 0:
            continue
        pert /= pert.sum()
        seeds.append(pert)

    # Deduplicate coarse (by rounding) and cap number
    uniq = []
    seen = set()
    for s in seeds:
        key = tuple(np.round(s, 6))
        if key not in seen:
            seen.add(key)
            uniq.append(s)
        if len(uniq) >= 34:
            break
    return uniq


def build_surrogate_weights(b: np.ndarray, tau_rel: float, eps_plateau: float, K: int, neighbor_radius: int) -> np.ndarray:
    """Construct plateau-aware Top-K union weights over lags, with inverse-pressure tail and top-R blending."""
    L = len(b)
    bmax = float(np.max(b))
    bmin = float(np.min(b))
    tau = tau_rel * (bmax - bmin + 1e-12)

    order = np.argsort(-b)
    topk = order[: min(K, L)]
    plateau_mask = b >= (1.0 - eps_plateau) * bmax
    S = np.where(plateau_mask)[0].tolist()

    active = set(S)
    for idx in list(S) + list(topk):
        for d in range(-neighbor_radius, neighbor_radius + 1):
            j = idx + d
            if 0 <= j < L:
                active.add(j)
    active = sorted(active)

    ws = np.zeros(L, dtype=np.float64)
    if active:
        logits = (b[active] - bmax) / max(1e-12, tau)
        ex = np.exp(np.clip(logits, -60.0, 0.0))
        sum_ex = ex.sum()
        ws_active = ex / sum_ex if sum_ex > 0 else np.full(len(active), 1.0 / len(active))
        ws[active] = ws_active
    else:
        ws[:] = 1.0 / L

    if len(order) >= 2:
        gap = (b[order[0]] - b[order[1]]) / (b[order[0]] + 1e-12)
    else:
        gap = 0.0
    # Gap-adaptive Top-R blending
    if gap < 0.0065:
        R = 3
        rho = 0.30
    elif gap < 0.012:
        R = 3
        rho = 0.22
    elif gap < 0.024:
        R = 2
        rho = 0.15
    else:
        R = 2
        rho = 0.08
    R = min(R, len(b))
    selR = order[:R]
    wR = np.zeros(L, dtype=np.float64)
    if R > 0:
        wR[selR] = 1.0 / R

    # Tail distribution: 50% uniform + 50% inverse pressure
    uniform = np.full(L, 1.0 / L)
    inv = 1.0 / (b - bmin + 1e-9)
    inv_sum = inv.sum()
    if inv_sum <= 0 or not np.isfinite(inv_sum):
        inv = uniform.copy()
    else:
        inv /= inv_sum
    tail = 0.5 * uniform + 0.5 * inv

    plateau_size = int(np.sum(plateau_mask))
    alpha0 = 0.09
    alpha = alpha0 + 0.07 * (1.0 - min(1.0, gap / 0.022)) + 0.03 * min(1.0, plateau_size / (0.22 * L))
    alpha = float(np.clip(alpha, 0.06, 0.26))

    w_core = (1.0 - rho) * ws + rho * wR
    w = (1.0 - alpha) * w_core + alpha * tail

    w = np.maximum(w, 0.0)
    sw = w.sum()
    if sw <= 0:
        w[:] = 1.0 / L
    else:
        w /= sw
    return w


def entropy_grad(a: np.ndarray, participation: np.ndarray, base_weight: float, beta: float) -> Tuple[float, np.ndarray]:
    """Weighted entropy penalty: sum wj * a_j * log a_j with weights wj = base_weight*(1 + beta*participation_norm)."""
    p = participation.copy()
    p = p - p.min()
    if p.max() > 0:
        p = p / p.max()
    w = base_weight * (1.0 + beta * p)
    a_safe = np.maximum(a, 1e-15)
    loss = float(np.dot(w, a_safe * np.log(a_safe)))
    grad = w * (1.0 + np.log(a_safe))
    return loss, grad


def compute_participation(a: np.ndarray, top_lags: List[int]) -> np.ndarray:
    """Compute index participation in the selected lags:
    participation[j] = sum_{k in top_lags} a[k - j] if in range.
    """
    n = len(a)
    part = np.zeros(n, dtype=np.float64)
    for k in top_lags:
        j_min = max(0, k - (n - 1))
        j_max = min(n - 1, k)
        if j_min <= j_max:
            idx_j = np.arange(j_min, j_max + 1)
            idx_a = k - idx_j
            part[idx_j] += a[idx_a]
    return part


def project_simplex(v: np.ndarray) -> np.ndarray:
    """Project onto the probability simplex: nonnegative, sum=1 (Euclidean projection)."""
    n = len(v)
    u = np.sort(v)[::-1]
    cssv = np.cumsum(u)
    rho = np.nonzero(u * np.arange(1, n + 1) > (cssv - 1))[0]
    if len(rho) == 0:
        w = np.maximum(v, 0.0)
        s = w.sum()
        return w / s if s > 0 else np.full(n, 1.0 / n)
    rho = rho[-1]
    theta = (cssv[rho] - 1.0) / (rho + 1)
    w = np.maximum(v - theta, 0.0)
    s = w.sum()
    if s <= 0:
        return np.full(n, 1.0 / n)
    return w / s


def hard_max_polish(a: np.ndarray, r: int = 3, steps: int = 6, init_lr: float = 0.18) -> Tuple[np.ndarray, float]:
    """A few projected steps to minimize the average of the top-r peaks. Accept only improving moves."""
    n = len(a)
    best_a = a.copy()
    best_F, b = exact_objective(a)
    order = np.argsort(-b)
    sel = order[: min(r, len(b))]
    w = np.zeros_like(b)
    w[sel] = 1.0 / len(sel)
    a_curr = a.copy()
    lr = init_lr
    for _ in range(steps):
        g = 2.0 * n * grad_corr_wrt_a(w, a_curr)
        a_trial = a_curr - lr * g
        a_trial = project_simplex(a_trial)
        F_trial, _ = exact_objective(a_trial)
        if F_trial < best_F - 1e-12:
            best_F = F_trial
            best_a = a_trial.copy()
            a_curr = a_trial
            lr *= 1.08
        else:
            lr *= 0.55
            if lr < 1e-7:
                break
    return best_a, best_F


def cutting_plane_refinement(a: np.ndarray, M: int = 36, steps: int = 56, init_lr: float = 0.11) -> Tuple[np.ndarray, float]:
    """Projected gradient refinement targeting the average of top-M lags (cutting-plane style).
    Accept only improving moves under the exact metric."""
    n = len(a)
    best_a = a.copy()
    best_F, b = exact_objective(a)
    order = np.argsort(-b)
    sel = order[: min(M, len(b))]
    w = np.zeros_like(b)
    w[sel] = 1.0 / len(sel)

    lr = init_lr
    a_curr = a.copy()
    for _ in range(steps):
        g = 2.0 * n * grad_corr_wrt_a(w, a_curr)
        a_trial = a_curr - lr * g
        a_trial = project_simplex(a_trial)
        F_trial, _ = exact_objective(a_trial)
        if F_trial < best_F - 1e-12:
            best_F = F_trial
            best_a = a_trial.copy()
            a_curr = a_trial
            lr *= 1.06
        else:
            lr *= 0.62
            if lr < 1e-7:
                break
    return best_a, best_F


def pair_shaving_transport(a: np.ndarray, b: np.ndarray, trials: int = 12, Q: int = 12, R: int = 3) -> Tuple[np.ndarray, float]:
    """Multi-pair transport: remove a tiny mass from top-Q contributors and redistribute to Q low-pressure indices.
    Participation computed using top-R worst lags. Accept only if F decreases.
    This version adds a distance bias away from the participation center."""
    n = len(a)
    F_curr = objective_from_b(b, n)
    order = np.argsort(-b)
    worst_lags = order[: min(R, len(b))].tolist()

    participation = compute_participation(a, worst_lags)
    score = participation * (a + 1e-12)
    top_idx = np.argsort(-score)[:Q]
    bottom_idx = np.argsort(score)[:Q]

    total_mass = float(np.minimum(0.025, 0.48 * a[top_idx].sum()))
    if total_mass <= 0:
        return a, F_curr

    if participation.sum() > 0:
        com = float(np.dot(np.arange(n), participation) / (participation.sum() + 1e-12))
    else:
        com = 0.5 * (n - 1)
    dist = np.abs(np.arange(n) - com) + 1.0
    dist = dist / dist.max()

    eps = total_mass
    best_a = a.copy()
    best_F = F_curr
    for _ in range(trials):
        remove_weights = a[top_idx] * (1.0 + score[top_idx] / (np.max(score[top_idx]) + 1e-12))
        rsum = remove_weights.sum()
        if rsum <= 0:
            break
        remove = eps * (remove_weights / rsum)
        a_prop = a.copy()
        a_prop[top_idx] -= remove

        add_weights_full = (1.0 - participation) * (1.0 - a) * (0.6 + 0.4 * dist)
        add_weights = add_weights_full[bottom_idx]
        add_weights = np.maximum(add_weights, 1e-12)
        add = eps * (add_weights / add_weights.sum())
        a_prop[bottom_idx] += add

        a_prop = np.maximum(a_prop, 0.0)
        a_prop = a_prop / max(1e-12, a_prop.sum())

        F_prop, _ = exact_objective(a_prop)
        if F_prop < best_F - 1e-12:
            best_F = F_prop
            best_a = a_prop
            eps *= 1.22
        else:
            eps *= 0.5
        if eps < 1e-8:
            break
    return best_a, best_F


def pressure_microstep_on_logits(s: np.ndarray, a: np.ndarray, b: np.ndarray, gap: float,
                                 eps_plateau: float, rng: np.random.Generator,
                                 eta: float = 0.035, tries: int = 3) -> Tuple[np.ndarray, float, np.ndarray]:
    """Tiny acceptance-tested microstep on logits s pushing away from current plateau peaks."""
    n = len(a)
    F_curr = objective_from_b(b, n)

    bmax = float(np.max(b))
    plateau_mask = b >= (1.0 - eps_plateau) * bmax
    S = np.where(plateau_mask)[0]
    if S.size == 0:
        return s, F_curr, b

    L = len(b)
    tau = max(1e-12, 0.004 * (bmax - float(np.min(b)) + 1e-12))
    logits = (b[S] - bmax) / tau
    ex = np.exp(np.clip(logits, -60.0, 0.0))
    ws = ex / max(1e-12, ex.sum())
    w = np.zeros(L, dtype=np.float64)
    w[S] = ws

    w = 0.92 * w + 0.08 * (np.ones_like(w) / L)
    w = w / max(1e-12, w.sum())

    g_sur_a = 2.0 * n * grad_corr_wrt_a(w, a)
    avg = float(np.dot(g_sur_a, a))
    g_s = a * (g_sur_a - avg)

    best_F = F_curr
    best_s = s
    best_b = b
    step = eta
    for _ in range(tries):
        s_trial = s - step * g_s
        a_trial = softmax(s_trial)
        F_trial, b_trial = exact_objective(a_trial)
        if F_trial < best_F - 1e-12:
            best_F = F_trial
            best_s = s_trial
            best_b = b_trial
            step *= 0.8
            s = best_s
            b = best_b
            F_curr = best_F
        else:
            step *= 0.5
            if step < 1e-6:
                break

    return best_s, best_F, best_b


def dual_peak_microstep_on_logits(s: np.ndarray, a: np.ndarray, b: np.ndarray,
                                  rng: np.random.Generator, eta: float = 0.032,
                                  tries: int = 3) -> Tuple[np.ndarray, float, np.ndarray]:
    """Acceptance-tested microstep that simultaneously targets the top-2/3 peaks with equal weights."""
    n = len(a)
    F_curr = objective_from_b(b, n)
    order = np.argsort(-b)
    if len(order) == 0:
        return s, F_curr, b
    gap = (b[order[0]] - b[order[1]]) / (b[order[0]] + 1e-12) if len(order) >= 2 else 0.0
    R = 3 if gap < 0.010 else 2
    sel = order[:R]
    w = np.zeros_like(b)
    w[sel] = 1.0 / R
    # Tiny jitter to avoid deadlock when peaks swap identities
    if R >= 2:
        j = rng.integers(0, R)
        w[sel[j]] *= 1.02

    g_sur_a = 2.0 * n * grad_corr_wrt_a(w, a)
    avg = float(np.dot(g_sur_a, a))
    g_s = a * (g_sur_a - avg)

    best_F = F_curr
    best_s = s
    best_b = b
    step = eta
    for _ in range(tries):
        s_trial = s - step * g_s
        a_trial = softmax(s_trial)
        F_trial, b_trial = exact_objective(a_trial)
        if F_trial < best_F - 1e-12:
            best_F = F_trial
            best_s = s_trial
            best_b = b_trial
            step *= 0.85
            s = best_s
            b = best_b
            F_curr = best_F
        else:
            step *= 0.55
            if step < 1e-6:
                break
    return best_s, best_F, best_b


def pnorm_polish(a: np.ndarray, p: float = 10.0, steps: int = 20, init_lr: float = 0.08) -> Tuple[np.ndarray, float]:
    """Projected polish minimizing a smooth surrogate close to max: ⟨w_p, b⟩ with w_p ∝ b^(p-1).
    Accepts only improvements under exact objective."""
    n = len(a)
    best_a = a.copy()
    best_F, b = exact_objective(a)
    bpos = np.maximum(b - np.min(b) + 1e-12, 1e-12)
    w = bpos ** (p - 1.0)
    w_sum = w.sum()
    if w_sum <= 0 or not np.isfinite(w_sum):
        w[:] = 1.0 / len(w)
    else:
        w /= w_sum

    a_curr = a.copy()
    lr = init_lr
    for _ in range(steps):
        g = 2.0 * n * grad_corr_wrt_a(w, a_curr)
        a_trial = a_curr - lr * g
        a_trial = project_simplex(a_trial)
        F_trial, b_trial = exact_objective(a_trial)
        if F_trial < best_F - 1e-12:
            best_F = F_trial
            best_a = a_trial.copy()
            a_curr = a_trial
            # Update w based on new b to remain close to max-norm
            bpos = np.maximum(b_trial - np.min(b_trial) + 1e-12, 1e-12)
            w = bpos ** (p - 1.0)
            w_sum = w.sum()
            if w_sum <= 0 or not np.isfinite(w_sum):
                w[:] = 1.0 / len(w)
            else:
                w /= w_sum
            lr *= 1.06
        else:
            lr *= 0.62
            if lr < 1e-7:
                break
    return best_a, best_F


def surrogate_descent(seed: np.ndarray, deadline: float, rng: np.random.Generator) -> Tuple[np.ndarray, float]:
    """Run the main optimization loop starting from a seed. Returns (best_a, best_score)."""
    n = len(seed)
    a = seed.copy()
    s = np.log(np.maximum(a, 1e-16))

    adam = AdamAMSGrad(dim=n, lr=0.055)
    grad_clip_norm = 6.5

    # Annealing and regularizer schedules (slightly sharpened, mild TV^2)
    tau_rel = 0.026        # start fairly sharp
    eps_plateau = 0.0085   # plateau band
    tv2_strength = 1.2e-3  # slightly smaller to reduce bias
    ent_base = 8.0e-4
    ent_beta = 0.90

    # Active-set controller parameters
    K = 16
    neighbor_radius = 2
    worst_idx_prev = None
    worst_change_count = 0

    best_a = a.copy()
    best_F, b = exact_objective(a)
    last_improv_iter = 0
    max_iters = 22000
    check_every = 20

    phase_len = 240
    it = 0
    while it < max_iters:
        if time.time() > deadline:
            break

        a = softmax(s)
        F, b = exact_objective(a)

        if F < best_F - 1e-12:
            best_F = F
            best_a = a.copy()
            last_improv_iter = it

        worst_idx = int(np.argmax(b))
        if worst_idx_prev is None:
            worst_idx_prev = worst_idx
        else:
            if worst_idx != worst_idx_prev:
                worst_change_count += 1
            worst_idx_prev = worst_idx

        order = np.argsort(-b)
        gap = (b[order[0]] - b[order[1]]) / (b[order[0]] + 1e-12) if len(order) >= 2 else 0.0
        plateau_mask = b >= (1.0 - eps_plateau) * b.max()
        plateau_size = int(np.sum(plateau_mask))
        if plateau_size > 0.12 * (2 * n - 1) or gap < 0.015:
            K = min(64, K + 2)
            neighbor_radius = min(6, 1 + plateau_size // 60)
        else:
            K = max(12, K - 1)
            neighbor_radius = max(1, neighbor_radius - 1)

        w = build_surrogate_weights(b, tau_rel=tau_rel, eps_plateau=eps_plateau, K=K, neighbor_radius=neighbor_radius)
        g_sur_a = 2.0 * n * grad_corr_wrt_a(w, a)

        R = 3 if gap < 0.012 else 2
        top_lags = order[: min(R, len(b))].tolist()
        part = compute_participation(a, top_lags)
        tv2_loss, tv2_grad_a = tv2_and_grad(a)
        ent_loss, ent_grad_a = entropy_grad(a, participation=part, base_weight=ent_base, beta=ent_beta)

        g_a = g_sur_a + tv2_strength * tv2_grad_a + ent_grad_a

        avg = float(np.dot(g_a, a))
        g_s = a * (g_a - avg)

        gnorm = np.linalg.norm(g_s)
        if not np.isfinite(gnorm) or gnorm > grad_clip_norm:
            if not np.isfinite(gnorm):
                g_s = np.nan_to_num(g_s, nan=0.0, posinf=0.0, neginf=0.0)
            else:
                g_s *= grad_clip_norm / (gnorm + 1e-12)

        step_vec = adam.step(g_s)
        s = s + step_vec

        # Plateau microstep
        if it % 14 == 7 and time.time() < deadline - 0.4:
            s, F_micro, b = pressure_microstep_on_logits(
                s=s, a=softmax(s), b=b, gap=gap, eps_plateau=eps_plateau, rng=rng, eta=0.031, tries=3
            )
            if F_micro < best_F - 1e-12:
                best_F = F_micro
                best_a = softmax(s)

        # Dual-peak microstep to suppress near-tied maxima
        if it % 21 == 10 and time.time() < deadline - 0.4:
            s, F_micro2, b = dual_peak_microstep_on_logits(
                s=s, a=softmax(s), b=b, rng=rng, eta=0.030, tries=3
            )
            if F_micro2 < best_F - 1e-12:
                best_F = F_micro2
                best_a = softmax(s)

        # Acceptance-tested symmetry mixing early to avoid pathological asymmetry
        if it % 60 == 0 and it < 1200 and time.time() < deadline - 0.4:
            a_tmp = softmax(s)
            gamma = 0.10 * (0.5 ** (it / 600.0))
            a_mix = (1.0 - gamma) * a_tmp + gamma * a_tmp[::-1]
            a_mix = a_mix / max(1e-12, a_mix.sum())
            F_mix, _ = exact_objective(a_mix)
            if F_mix < best_F - 1e-12:
                best_F = F_mix
                best_a = a_mix.copy()
                s = np.log(np.maximum(best_a, 1e-16))

        # Hard moves interleaving: polish, cutting-plane, transport
        if it % phase_len == phase_len - 210 and time.time() < deadline - 0.4:
            a_tmp = softmax(s)
            a_hm, F_hm = hard_max_polish(a_tmp, r=2, steps=5, init_lr=0.14)
            if F_hm < best_F - 1e-12:
                best_F = F_hm
                best_a = a_hm
                s = np.log(np.maximum(best_a, 1e-16))

        if it % phase_len == phase_len - 150 and time.time() < deadline - 0.4:
            a_tmp = softmax(s)
            a_polish, F_polish = hard_max_polish(a_tmp, r=3, steps=6, init_lr=0.16)
            if F_polish < best_F - 1e-12:
                best_F = F_polish
                best_a = a_polish
                s = np.log(np.maximum(best_a, 1e-16))

        if it % phase_len == phase_len - 100 and time.time() < deadline - 0.4:
            a_tmp = softmax(s)
            M = 32 if plateau_size < 0.10 * (2 * n - 1) else 40
            a_cp, F_cp = cutting_plane_refinement(a_tmp, M=M, steps=48, init_lr=0.10)
            if F_cp < best_F - 1e-12:
                best_F = F_cp
                best_a = a_cp
                s = np.log(np.maximum(best_a, 1e-16))

        if it % phase_len == phase_len - 50 and time.time() < deadline - 0.4:
            a_tmp = softmax(s)
            F_tmp, b_tmp = exact_objective(a_tmp)
            Q = 12 if plateau_size < 0.10 * (2 * n - 1) else 14
            a_ps, F_ps = pair_shaving_transport(a_tmp, b_tmp, trials=10, Q=Q, R=3)
            if F_ps < best_F - 1e-12:
                best_F = F_ps
                best_a = a_ps
                s = np.log(np.maximum(best_a, 1.0e-16))

        # Stagnation-triggered hard triad + p-norm polish
        if it - last_improv_iter > 650 and time.time() < deadline - 0.6:
            a_tmp = softmax(s)
            a_polish, F_polish = hard_max_polish(a_tmp, r=3, steps=6, init_lr=0.15)
            if F_polish < best_F - 1e-12:
                best_F = F_polish
                best_a = a_polish
                s = np.log(np.maximum(best_a, 1e-16))
            a_cp, F_cp = cutting_plane_refinement(best_a, M=36, steps=42, init_lr=0.095)
            if F_cp < best_F - 1e-12:
                best_F = F_cp
                best_a = a_cp
                s = np.log(np.maximum(best_a, 1e-16))
            F_best_tmp, b_best_tmp = exact_objective(best_a)
            a_ps, F_ps = pair_shaving_transport(best_a, b_best_tmp, trials=8, Q=12, R=3)
            if F_ps < best_F - 1e-12:
                best_F = F_ps
                best_a = a_ps
                s = np.log(np.maximum(best_a, 1e-16))
            a_pn, F_pn = pnorm_polish(best_a, p=10.0, steps=16, init_lr=0.075)
            if F_pn < best_F - 1e-12:
                best_F = F_pn
                best_a = a_pn
                s = np.log(np.maximum(best_a, 1e-16))
            last_improv_iter = it

        if it % check_every == 0 and it > 0:
            tau_rel = max(0.0035, tau_rel * 0.996)
            eps_plateau = max(0.0030, eps_plateau * 0.997)
            tv2_strength = max(6.5e-7, tv2_strength * 0.997)
            ent_base = max(3.0e-5, ent_base * 0.995)
            ent_beta = max(0.20, ent_beta * 0.997)

            if it - last_improv_iter > 900 or worst_change_count > 7:
                adam.set_lr(max(0.006, adam.lr * 0.7))
                worst_change_count = 0
                last_improv_iter = it

        it += 1

    # Final continuation: polish, cutting-plane, transport, p-norm, symmetry
    a_final = softmax(s)
    F_final, _ = exact_objective(a_final)
    if F_final < best_F - 1e-12:
        best_F = F_final
        best_a = a_final

    a_polish, F_polish = hard_max_polish(best_a, r=3, steps=6, init_lr=0.12)
    if F_polish < best_F - 1e-12:
        best_F = F_polish
        best_a = a_polish
    a_cp, F_cp = cutting_plane_refinement(best_a, M=40, steps=35, init_lr=0.085)
    if F_cp < best_F - 1e-12:
        best_F = F_cp
        best_a = a_cp
    F_best_tmp, b_best_tmp = exact_objective(best_a)
    a_ps, F_ps = pair_shaving_transport(best_a, b_best_tmp, trials=6, Q=10, R=3)
    if F_ps < best_F - 1e-12:
        best_F = F_ps
        best_a = a_ps
    a_pn, F_pn = pnorm_polish(best_a, p=12.0, steps=14, init_lr=0.07)
    if F_pn < best_F - 1e-12:
        best_F = F_pn
        best_a = a_pn

    a_sym = 0.96 * best_a + 0.04 * best_a[::-1]
    a_sym = a_sym / max(1e-12, a_sym.sum())
    F_sym, _ = exact_objective(a_sym)
    if F_sym < best_F - 1e-12:
        best_F = F_sym
        best_a = a_sym

    best_a = np.maximum(best_a, 0.0)
    ssum = best_a.sum()
    if not np.isfinite(ssum) or ssum <= 0:
        x = np.linspace(-0.25, 0.25, n)
        step_function = 1.0 + 4.0 * np.abs(x) - 16.0 * x**2
        step_function = (step_function + step_function[::-1]) / 2.0
        step_function = np.maximum(step_function, 0.0)
        step_function /= np.sum(step_function)
        return step_function, objective_from_b(self_convolution(step_function), n)
    best_a /= ssum
    return best_a, best_F


def resample_sequence(a: np.ndarray, new_n: int) -> np.ndarray:
    """Linear interpolation resampling to new_n length, preserving nonnegativity and normalization."""
    old_n = len(a)
    if old_n == new_n:
        return a.copy()
    x_old = np.linspace(0.0, 1.0, old_n)
    x_new = np.linspace(0.0, 1.0, new_n)
    a_new = np.interp(x_new, x_old, a)
    a_new = np.maximum(a_new, 0.0)
    s = a_new.sum()
    if s <= 0 or not np.isfinite(s):
        return np.full(new_n, 1.0 / new_n)
    return a_new / s


def multi_resolution_bootstrap(n_target: int, rng: np.random.Generator, deadline: float) -> List[np.ndarray]:
    """Run brief coarse optimizations at smaller n and upsample to n_target to provide strong seeds."""
    seeds_out = []
    coarse_sizes = [320, 360, 420]
    time_left = max(0.0, deadline - time.time())
    if time_left < 2.0:
        return seeds_out
    per_total = min(32.0, 0.08 * time_left)
    per_each = max(6.0, per_total / len(coarse_sizes))

    for n_c in coarse_sizes:
        if time.time() > deadline:
            break
        seeds_c = build_seeds(n_c, rng)
        scores = []
        for s0 in seeds_c:
            F0, _ = exact_objective(s0)
            scores.append(F0)
        order = np.argsort(scores)
        top_ids = order[: min(6, len(order))]
        per_seed_deadline = min(deadline, time.time() + per_each)
        best_c = None
        best_Fc = np.inf
        for i in top_ids:
            if time.time() > per_seed_deadline:
                break
            a0 = seeds_c[i]
            dl = min(per_seed_deadline, time.time() + (per_each / len(top_ids)))
            a_opt_c, F_opt_c = surrogate_descent(a0, dl, rng)
            if F_opt_c < best_Fc:
                best_Fc = F_opt_c
                best_c = a_opt_c
        if best_c is not None:
            up = resample_sequence(best_c, n_target)
            seeds_out.append(up)
    return seeds_out


def build_arch_with_scale(n: int, s: float) -> np.ndarray:
    """Build symmetric parabolic arch parameterized by width scale s over [-1/4, 1/4]."""
    x = np.linspace(-0.25, 0.25, n)
    xs = x / s
    arch = 1.0 + 4.0 * np.abs(xs) - 16.0 * (xs ** 2)
    arch = np.maximum(arch, 0.0)
    arch = 0.5 * (arch + arch[::-1])
    ssum = arch.sum()
    if ssum <= 0 or not np.isfinite(ssum):
        return np.full(n, 1.0 / n)
    return arch / ssum


def select_width_swept_best_seed(n: int) -> np.ndarray:
    """Width-swept seed selection over s ∈ {0.75, 0.85, 0.95, 1.05, 1.15}. Evaluate exact objective."""
    scales = [0.75, 0.85, 0.95, 1.05, 1.15]
    best_a = None
    best_F = np.inf
    for s in scales:
        a = build_arch_with_scale(n, s)
        F, _ = exact_objective(a)
        if F < best_F:
            best_F = F
            best_a = a
    if best_a is None:
        best_a = build_arch_with_scale(n, 1.0)
    return best_a


def accepted_peak_shaving_microsteps(a: np.ndarray, max_shaves: int = 3) -> np.ndarray:
    """Perform up to max_shaves peak-shaving microsteps on a, strictly acceptance-tested."""
    n = len(a)
    a_curr = a.copy()
    for _ in range(max_shaves):
        F_curr, b = exact_objective(a_curr)
        k = int(np.argmax(b))
        i_min = max(0, k - (n - 1))
        i_max = min(n - 1, k)
        if i_min > i_max:
            break
        idx_i = np.arange(i_min, i_max + 1)
        idx_j = k - idx_i
        prod = a_curr[idx_i] * a_curr[idx_j]
        if prod.size == 0:
            break
        pmax = int(np.argmax(prod))
        i_star = int(idx_i[pmax])
        j_star = int(idx_j[pmax])

        r = float(min(0.002, a_curr[i_star], a_curr[j_star]))
        if r <= 0.0:
            continue

        improved = False
        tries = 8
        for _t in range(tries):
            candidates = []
            ip1 = i_star + 1
            jp1 = j_star - 1
            if 0 <= ip1 < n and 0 <= jp1 < n:
                c1 = a_curr.copy()
                c1[i_star] -= r
                c1[j_star] -= r
                c1[ip1] += r
                c1[jp1] += r
                c1 = np.maximum(c1, 0.0)
                ssum = c1.sum()
                if ssum > 0 and np.isfinite(ssum):
                    c1 /= ssum
                    candidates.append(c1)
            ip2 = i_star - 1
            jp2 = j_star + 1
            if 0 <= ip2 < n and 0 <= jp2 < n:
                c2 = a_curr.copy()
                c2[i_star] -= r
                c2[j_star] -= r
                c2[ip2] += r
                c2[jp2] += r
                c2 = np.maximum(c2, 0.0)
                ssum = c2.sum()
                if ssum > 0 and np.isfinite(ssum):
                    c2 /= ssum
                    candidates.append(c2)

            accepted = False
            for cand in candidates:
                F_cand, _ = exact_objective(cand)
                if F_cand < F_curr - 1e-12:
                    a_curr = cand
                    improved = True
                    accepted = True
                    break
            if accepted:
                break
            r *= 0.5
            if r < 1e-9:
                break
        if not improved:
            break
    return a_curr


def search_for_best_sequence():
    """Run the multi-seed optimization with time management and return the best sequence found.

    Steps:
      - Build a width-swept arch and apply accepted peak-shaving microsteps.
      - Add multi-resolution bootstrap seeds.
      - Build additional diverse seeds and prioritize them by exact objective.
      - Optimize each prioritized seed under time budget with surrogate descent and hard moves.
    """
    n = 600
    rng = np.random.default_rng(42)
    total_time_budget = 1000.0
    safety_margin = 4.0
    deadline = time.time() + total_time_budget - safety_margin

    try:
        arch_best = select_width_swept_best_seed(n)
        arch_best = accepted_peak_shaving_microsteps(arch_best, max_shaves=3)
    except Exception:
        arch_best = build_arch_with_scale(n, 1.0)

    try:
        mr_seeds = multi_resolution_bootstrap(n_target=n, rng=rng, deadline=deadline)
    except Exception:
        mr_seeds = []

    seeds = [arch_best] + build_seeds(n, rng) + mr_seeds

    seed_scores = []
    for a0 in seeds:
        F0, _ = exact_objective(a0)
        seed_scores.append(F0)
    order = np.argsort(seed_scores)
    seeds = [seeds[i] for i in order]

    best_a = seeds[0].copy()
    best_F, _ = exact_objective(best_a)

    num_seeds = max(1, len(seeds))
    base_slice = max(6.0, (total_time_budget - safety_margin) / (2.2 * num_seeds))

    for idx, seed in enumerate(seeds):
        if time.time() > deadline:
            break
        factor = 2.2 if idx < 2 else (1.6 if idx < 6 else 1.0)
        per_seed_deadline = min(deadline, time.time() + base_slice * factor)

        a_opt, F_opt = surrogate_descent(seed, per_seed_deadline, rng)
        if F_opt < best_F - 1e-12:
            best_F = F_opt
            best_a = a_opt

        if time.time() < deadline - 1.0:
            a_polish, F_polish = hard_max_polish(best_a, r=3, steps=6, init_lr=0.12)
            if F_polish < best_F - 1e-12:
                best_F = F_polish
                best_a = a_polish
            a_cp, F_cp = cutting_plane_refinement(best_a, M=40, steps=30, init_lr=0.08)
            if F_cp < best_F - 1e-12:
                best_F = F_cp
                best_a = a_cp
            a_pn, F_pn = pnorm_polish(best_a, p=12.0, steps=12, init_lr=0.07)
            if F_pn < best_F - 1e-12:
                best_F = F_pn
                best_a = a_pn

    best_a = np.maximum(best_a, 0.0)
    s = best_a.sum()
    if not np.isfinite(s) or s <= 0:
        x = np.linspace(-0.25, 0.25, n)
        step_function = 1.0 + 4.0 * np.abs(x) - 16.0 * x**2
        step_function = (step_function + step_function[::-1]) / 2.0
        step_function = np.maximum(step_function, 0.0)
        step_function /= np.sum(step_function)
        return step_function
    best_a /= s
    return best_a
# EVOLVE_END


def evaluate_sequence(sequence: list[float]) -> float:
    """
    Evaluates a sequence of coefficients with enhanced security checks.
    Returns np.inf if the input is invalid.
    """
    # --- Security Checks ---

    # Verify that the input is a list
    if not isinstance(sequence, list):
        return np.inf

    # Reject empty lists
    if not sequence:
        return np.inf

    # Check each element in the list for validity
    for x in sequence:
        # Reject boolean types (as they are a subclass of int) and
        # any other non-integer/non-float types (like strings or complex numbers).
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return np.inf

        # Reject Not-a-Number (NaN) and infinity values.
        if np.isnan(x) or np.isinf(x):
            return np.inf

    # Convert all elements to float for consistency
    sequence = [float(x) for x in sequence]

    # Protect against negative numbers
    sequence = [max(0, x) for x in sequence]

    # Protect against numbers that are too large
    sequence = [min(1000.0, x) for x in sequence]

    n = len(sequence)
    b_sequence = np.convolve(sequence, sequence)
    max_b = max(b_sequence)
    sum_a = np.sum(sequence)

    # Protect against the case where the sum is too close to zero
    if sum_a < 0.01:
        return np.inf

    return float(2 * n * max_b / (sum_a**2))


def run_search_for_best_sequence():
    # Run the optimizer and return the best sequence found as a Python list
    best = search_for_best_sequence()
    # Final safety normalization and type conversion
    best = np.maximum(best, 0.0)
    s = best.sum()
    if not np.isfinite(s) or s <= 0:
        # Fallback to simple uniform sequence if something went wrong
        best = np.full(600, 1.0 / 600.0, dtype=np.float64)
    else:
        best = best / s
    return [float(x) for x in best.tolist()]


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))
```
