Design choices and strategies that yielded a verified upper bound <= 0.3521 with robust performance on the Hermite-combination search task.

- Hermite-CMA-Roots: Global–Local Hybrid Search for Minimizing Largest Positive Hermite Root: By optimizing the exact objective—the largest positive real root of P(x)/x^2 for P(x)=c0 H0(x)+c1 H4(x)+c2 H8(x)+c3 H12(x) with c3 chosen to enforce P(0)=0—the method aligned search pressure with the true bound r^2/(2π) and avoided surrogate objectives.
- Hermite-CMA-Roots: Global–Local Hybrid Search for Minimizing Largest Positive Hermite Root: A hybrid strategy that couples fast global exploration with precise local refinement, together with coefficient normalization to remove scaling degeneracy, reliably discovered high-quality minima in the 3D coefficient space.
- Hermite-CMA-Roots: Global–Local Hybrid Search for Minimizing Largest Positive Hermite Root: This design achieved c_upper_bound = 0.3520991045051883 with validity 1.0 and eval_time 0.9077494399971329 s, satisfying the ≤ 0.3521 target.

```python
#!/usr/bin/env python3
"""Search for Hermite-combination coefficients yielding an improved upper bound.

This implements a targeted global-local optimization over coefficients (c0, c1, c2)
for P(x) = c0 H0(x) + c1 H4(x) + c2 H8(x) + c3 H12(x), with c3 imposed by P(0) = 0.
The objective is the upper bound r^2 / (2*pi), where r is the largest positive real
root of P(x)/x^2. We search directly for small upper bounds and return coefficients
that are expected to pass the provided verification routine.

Notes:
- We use physicists' Hermite polynomials via explicit coefficient recurrence.
- The final coefficient c3 is determined by P(0) = 0, i.e.,
    c3 = -(c0*H0(0) + c1*H4(0) + c2*H8(0)) / H12(0).
- Scaling all coefficients leaves roots invariant, so we normalize during search.
- We combine a random global search and a compact Nelder–Mead local refinement.
"""

import json
import math
import random
from typing import List, Tuple

import numpy as np


# ==========================
# Hermite polynomial helpers
# ==========================
def hermite_phys_coeffs_up_to(n_max: int) -> List[np.ndarray]:
    """Compute coefficient arrays (ascending powers) for physicists' Hermite H_n(x) up to n_max.

    Returns:
      coeffs: list where coeffs[n] is an ndarray representing H_n(x) in monomial basis:
              coeffs[n][k] is the coefficient of x^k.
    Recurrence: H_{n+1} = 2x H_n - 2n H_{n-1}.
    """
    coeffs = []
    # H0 = 1
    H0 = np.zeros(1 + 0, dtype=np.float64)
    H0 = np.array([1.0], dtype=np.float64)
    coeffs.append(H0)
    if n_max == 0:
        return coeffs

    # H1 = 2x
    H1 = np.array([0.0, 2.0], dtype=np.float64)
    coeffs.append(H1)

    for n in range(1, n_max):
        # 2x * H_n
        Hn = coeffs[n]
        Hn_shift = np.concatenate([np.array([0.0], dtype=np.float64), Hn])
        term1 = 2.0 * Hn_shift
        # -2n * H_{n-1}
        term2 = -2.0 * n * coeffs[n - 1].copy()
        # pad term2 to match length
        if term2.shape[0] < term1.shape[0]:
            pad = np.zeros(term1.shape[0] - term2.shape[0], dtype=np.float64)
            term2 = np.concatenate([term2, pad])
        elif term1.shape[0] < term2.shape[0]:
            pad = np.zeros(term2.shape[0] - term1.shape[0], dtype=np.float64)
            term1 = np.concatenate([term1, pad])
        coeffs.append(term1 + term2)
    return coeffs


def poly_add(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Add two polynomials a(x) and b(x) in ascending monomial basis."""
    if a.shape[0] < b.shape[0]:
        a = np.pad(a, (0, b.shape[0] - a.shape[0]))
    elif b.shape[0] < a.shape[0]:
        b = np.pad(b, (0, a.shape[0] - b.shape[0]))
    return a + b


def poly_scale(a: np.ndarray, s: float) -> np.ndarray:
    return a * s


def drop_leading_zeros_desc(coeff_desc: np.ndarray, tol: float = 1e-14) -> np.ndarray:
    """Drop leading near-zero coefficients from a polynomial in descending order."""
    if coeff_desc.size == 0:
        return coeff_desc
    idx = 0
    n = coeff_desc.size
    while idx < n and abs(coeff_desc[idx]) <= tol:
        idx += 1
    if idx >= n:
        return np.array([0.0], dtype=np.float64)
    return coeff_desc[idx:]


# ==========================
# Objective (upper bound)
# ==========================
class HermiteBoundObjective:
    def __init__(self):
        # Precompute Hermite polynomials up to degree 12 in monomial basis.
        self.hermite_coeffs = hermite_phys_coeffs_up_to(12)
        # Extract H0,H4,H8,H12 coefficients.
        self.H0 = self.hermite_coeffs[0].copy()   # degree 0
        self.H4 = self.hermite_coeffs[4].copy()   # degree 4
        self.H8 = self.hermite_coeffs[8].copy()   # degree 8
        self.H12 = self.hermite_coeffs[12].copy()  # degree 12
        # Values at 0 for even Hermite (physicists):
        # H_{2k}(0) = (-1)^k (2k)! / k!
        # For k = 0,2,4,6 respectively:
        self.H0_0 = 1.0
        self.H4_0 = 12.0
        self.H8_0 = 1680.0
        self.H12_0 = 665280.0
        self.two_pi = 2.0 * math.pi

    def build_poly_coeffs(self, c0: float, c1: float, c2: float) -> Tuple[np.ndarray, float]:
        """Construct P(x) coefficients in ascending order and return (P, c3).

        P(x) = c0*H0 + c1*H4 + c2*H8 + c3*H12 where c3 ensures P(0) = 0.
        """
        # Enforce P(0)=0 by choosing c3 accordingly (exact numbers for stability).
        c3 = -(c0 * self.H0_0 + c1 * self.H4_0 + c2 * self.H8_0) / self.H12_0
        P = np.zeros_like(self.H12, dtype=np.float64)
        P = poly_add(P, poly_scale(self.H0, c0))
        P = poly_add(P, poly_scale(self.H4, c1))
        P = poly_add(P, poly_scale(self.H8, c2))
        P = poly_add(P, poly_scale(self.H12, c3))
        return P, c3

    def largest_positive_root_div_by_x2(self, P: np.ndarray) -> float:
        """Compute the largest positive real root of P(x)/x^2.

        P is even and satisfies P(0)=0, so P(x) = x^2 * Q(x) where Q is even degree-10.
        We extract Q by dropping the first two ascending coefficients and then find roots.
        Returns +inf if no positive real root is found.
        """
        if P.shape[0] < 3:
            return float("inf")
        # Drop constant and x^1 terms to divide by x^2:
        Q = P[2:].copy()
        # Handle case where highest terms are nearly zero: reduce degree safely.
        # numpy.roots expects descending coefficients:
        coeff_desc = Q[::-1].copy()
        coeff_desc = drop_leading_zeros_desc(coeff_desc, tol=1e-14)
        if coeff_desc.size <= 1:
            return float("inf")
        try:
            roots = np.roots(coeff_desc)
        except Exception:
            return float("inf")
        # Filter real positive roots
        real_roots = roots[np.isfinite(roots)]
        # numeric tolerance for imaginary part
        tol_im = 1e-8
        real_roots = real_roots[np.abs(real_roots.imag) <= tol_im].real
        if real_roots.size == 0:
            return float("inf")
        pos_roots = real_roots[real_roots > 0.0]
        if pos_roots.size == 0:
            return float("inf")
        return float(np.max(pos_roots))

    def upper_bound(self, cvec: np.ndarray) -> float:
        """Compute the upper bound r^2 / (2*pi) from coefficients cvec=[c0,c1,c2]."""
        c0, c1, c2 = float(cvec[0]), float(cvec[1]), float(cvec[2])
        # Build P and ensure division by x^2 is valid
        P, _ = self.build_poly_coeffs(c0, c1, c2)
        r = self.largest_positive_root_div_by_x2(P)
        if not np.isfinite(r) or r <= 0:
            return 1e9
        return (r * r) / self.two_pi

    def objective(self, cvec: np.ndarray) -> float:
        """Objective to minimize; uses normalized coefficient vector to remove scaling degeneracy."""
        # Normalize to unit 2-norm to improve conditioning, but compute bound on normalized vector.
        norm = float(np.linalg.norm(cvec))
        if not np.isfinite(norm) or norm <= 1e-14:
            return 1e9
        c = cvec / norm
        return self.upper_bound(c)


# ==========================
# Optimizer: Nelder–Mead
# ==========================
def nelder_mead(fun, x0: np.ndarray, step: float = 0.25, max_iter: int = 300, tol: float = 1e-9) -> Tuple[np.ndarray, float]:
    """A compact Nelder–Mead implementation for local refinement in R^3."""
    n = x0.size
    # Initialize simplex
    simplex = np.zeros((n + 1, n), dtype=np.float64)
    simplex[0] = x0.copy()
    for i in range(n):
        y = x0.copy()
        y[i] += step
        simplex[i + 1] = y
    vals = np.array([fun(simplex[i]) for i in range(n + 1)], dtype=np.float64)

    alpha = 1.0
    gamma = 2.0
    rho = 0.5
    sigma = 0.5

    for _ in range(max_iter):
        # Order
        idx = np.argsort(vals)
        simplex = simplex[idx]
        vals = vals[idx]

        # Stopping criterion: simplex size and function spread
        edge = np.max(np.linalg.norm(simplex[1:] - simplex[0], axis=1))
        fspread = np.max(vals) - np.min(vals)
        if edge < tol and fspread < tol:
            break

        # Centroid of best n points
        centroid = np.mean(simplex[:-1], axis=0)

        # Reflection
        xr = centroid + alpha * (centroid - simplex[-1])
        fr = fun(xr)

        if fr < vals[0]:
            # Expansion
            xe = centroid + gamma * (xr - centroid)
            fe = fun(xe)
            if fe < fr:
                simplex[-1], vals[-1] = xe, fe
            else:
                simplex[-1], vals[-1] = xr, fr
        elif fr < vals[-2]:
            simplex[-1], vals[-1] = xr, fr
        else:
            # Contraction
            if fr < vals[-1]:
                # Outside contraction
                xc = centroid + rho * (xr - centroid)
            else:
                # Inside contraction
                xc = centroid - rho * (centroid - simplex[-1])
            fc = fun(xc)
            if fc < vals[-1]:
                simplex[-1], vals[-1] = xc, fc
            else:
                # Shrink
                best = simplex[0].copy()
                for i in range(1, n + 1):
                    simplex[i] = best + sigma * (simplex[i] - best)
                    vals[i] = fun(simplex[i])

    # Return best
    idx = np.argmin(vals)
    return simplex[idx], float(vals[idx])


# ==========================
# Global search strategy
# ==========================
def global_local_search(obj: HermiteBoundObjective, target: float = 0.3521) -> Tuple[np.ndarray, float]:
    """Run a hybrid global-local search for coefficients achieving small upper bound.

    Returns:
      best_c (np.ndarray), best_val (float)
    """
    rng = np.random.default_rng(123456789)
    best_c = None
    best_val = float("inf")

    def eval_and_update(cvec):
        nonlocal best_c, best_val
        val = obj.objective(cvec)
        if val < best_val:
            best_val = val
            best_c = cvec / np.linalg.norm(cvec)
        return val

    # Seed set: include some structured seeds (basis, combinations) to diversify search.
    seeds = []
    bases = [
        np.array([1.0, 0.0, 0.0], dtype=np.float64),
        np.array([0.0, 1.0, 0.0], dtype=np.float64),
        np.array([0.0, 0.0, 1.0], dtype=np.float64),
        np.array([1.0, -0.5, 0.1], dtype=np.float64),
        np.array([1.0, -1.0, 0.5], dtype=np.float64),
        np.array([0.2, -0.8, 1.0], dtype=np.float64),
        np.array([-0.5, 1.2, 1.0], dtype=np.float64),
        np.array([-0.4, 0.7, -1.0], dtype=np.float64),
    ]
    seeds.extend(bases)

    # Random unit vectors (global exploration)
    N_rand = 4000
    gauss = rng.normal(size=(N_rand, 3))
    norms = np.linalg.norm(gauss, axis=1, keepdims=True)
    gauss = gauss / np.clip(norms, 1e-12, None)
    seeds.extend(list(gauss))

    # Evaluate all seeds and keep the top K
    vals = []
    for s in seeds:
        val = eval_and_update(s)
        vals.append((val, s))
        # Early exit if target achieved
        if best_val <= target:
            return best_c, best_val

    vals.sort(key=lambda t: t[0])
    topK = [s for (_, s) in vals[:30]]

    # Local refinement (Nelder–Mead) from best K seeds
    for s in topK:
        # Use two stages of local refinement with different step sizes
        x, fx = nelder_mead(obj.objective, s, step=0.2, max_iter=180, tol=1e-10)
        if fx < best_val:
            best_val = fx
            best_c = x / np.linalg.norm(x)
        if best_val <= target:
            break
        x, fx = nelder_mead(obj.objective, best_c, step=0.05, max_iter=220, tol=1e-12)
        if fx < best_val:
            best_val = fx
            best_c = x / np.linalg.norm(x)
        if best_val <= target:
            break

    return best_c, best_val


def rationalize_coeffs(c: np.ndarray) -> List[float]:
    """Round coefficients to modest-precision floats for stable symbolic handling downstream."""
    # Scaling to keep numbers moderate (not needed for roots, but helps presentation).
    # Since scaling doesn't affect roots, we can rescale so max |c_i| is about 1.
    scale = np.max(np.abs(c))
    if not np.isfinite(scale) or scale < 1e-14:
        scale = 1.0
    c_scaled = c / scale
    # Round to 12 decimal places to ensure compact representation while remaining accurate.
    return [float(np.round(ci, 12)) for ci in c_scaled]


# EVOLVE_START
def find_coefficients():
    """Return coefficients [c0, c1, c2] that verify and give an improved upper bound.

    Strategy:
      - Optimize the largest positive real root of P(x)/x^2, with P built from H0,H4,H8,H12
        and P(0)=0 enforced automatically by the verifier.
      - Conduct a global random search followed by deterministic local refinements.
      - If we find a candidate yielding an upper bound <= 0.3521, stop early.

    Returns:
      A list [c0, c1, c2] (floats) suitable for the verification function.
    """
    obj = HermiteBoundObjective()
    target = 0.3521

    best_c, best_val = global_local_search(obj, target=target)

    # Safety fallback in the unlikely event search fails (return a sane baseline)
    if best_c is None or not np.isfinite(best_val):
        # Simple deterministic baseline (won't meet best target, but verifies)
        return [1.0, -1.0, 0.5]

    # Optionally perform a final polish around the current best with a tightened step.
    x, fx = nelder_mead(obj.objective, best_c, step=0.02, max_iter=250, tol=1e-13)
    if fx < best_val:
        best_c = x / np.linalg.norm(x)
        best_val = fx

    # Produce rounded floats for stable sympy Rational conversion
    coeffs = rationalize_coeffs(best_c)

    # Ensure non-pathological output (e.g., avoid -0.0)
    coeffs = [0.0 if abs(v) < 1e-15 else v for v in coeffs]

    return coeffs
# EVOLVE_END


if __name__ == "__main__":
    print(json.dumps({"coefficients": find_coefficients()}))
```
