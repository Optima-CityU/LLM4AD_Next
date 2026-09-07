#!/usr/bin/env python3
"""Hermite coefficients search for the uncertainty inequality upper bound.

This script implements a global-to-local optimization to find coefficients
(c0, c1, c2) for a Hermite polynomial combination P(x) built from H_0, H_4,
H_8 (and an automatically appended H_12 by the verifier to enforce P(0)=0).
The objective is to minimize the largest positive root r_max of P(x)/x^2,
so that the upper bound on C4 equals r_max^2 / (2*pi), aiming for <= 0.3521.

The function find_coefficients() returns a list of three numbers (preferably
rational strings) that will be validated by the verify_hermite_combination
function provided externally.
"""

import json


# EVOLVE_START
def find_coefficients():
    """
    Search for coefficients (c0, c1, c2) that minimize the largest positive
    root of P(x)/x^2, where P(x) = c0*H_0(x) + c1*H_4(x) + c2*H_8(x) + c3*H_12(x),
    and c3 is chosen so that P(0) = 0 (as the verifier will do). We enforce
    scale invariance by normalizing (c0, c1, c2) to unit norm, and use
    quasi-global exploration with seeded 1D scans and DE-like refinement,
    followed by Nelder–Mead polishing and stochastic local refinement.
    A Newton polishing step refines the largest positive root for accuracy.

    Returns:
        A list [c0, c1, c2] with entries given as rational strings to ensure
        exact handling by sympy.Rational in the verifier.
    """
    import math
    import random
    from typing import List, Tuple

    import numpy as np
    import sympy

    # Determinism
    rng = random.Random(20260831)
    np.random.seed(20260831)

    # Build Hermite basis H_0, H_4, H_8, H_12 using SymPy's physicists' Hermite polynomials
    x = sympy.symbols('x')
    degrees = [0, 4, 8, 12]
    hps = [sympy.polys.orthopolys.hermite_poly(n=i, x=x, polys=False) for i in degrees]
    polys = [sympy.Poly(h, x, domain='QQ') for h in hps]

    # Convert SymPy Poly to monomial coefficient array of a given max length (descending powers)
    def poly_to_array(p: sympy.Poly, length: int) -> np.ndarray:
        # p.as_dict() maps monomials to coefficients, keys like {(k,): coeff}
        dct = p.as_dict()
        arr = []
        for deg in range(length - 1, -1, -1):
            coeff = dct.get((deg,), sympy.Integer(0))
            arr.append(float(coeff))
        return np.array(arr, dtype=float)

    # Max degree is 12 -> length 13 from x^12 down to x^0
    basis_arrays = [poly_to_array(p, 13) for p in polys]
    A0, A4, A8, A12 = basis_arrays

    # Values at 0 for computing appended coefficient c3 that enforces P(0)=0
    h0_0 = float(hps[0].subs(x, 0))
    h4_0 = float(hps[1].subs(x, 0))
    h8_0 = float(hps[2].subs(x, 0))
    h12_0 = float(hps[3].subs(x, 0))

    # Utility to compute the monomial coefficients of P(x) for a given (c0, c1, c2)
    # with the appended c3 chosen so that P(0)=0. Returns monomial array (deg 12..0).
    def build_P_coeffs(c0: float, c1: float, c2: float) -> np.ndarray:
        # Enforce P(0)=0 by appending H12 with c3
        c3 = - (c0 * h0_0 + c1 * h4_0 + c2 * h8_0) / h12_0
        P = c0 * A0 + c1 * A4 + c2 * A8 + c3 * A12
        # Numerical guard: ensure constant term x^0 is exactly zero
        P[-1] = 0.0
        # And x^1 term is already 0 for even polynomials
        return P

    # Horner evaluation (value and derivative) for a polynomial with descending coefficients.
    # coeffs represent degrees N..0 (contiguous).
    def eval_poly_and_der_desc(coeffs: np.ndarray, xval: float) -> Tuple[float, float]:
        y = coeffs[0]
        dy = 0.0
        for c in coeffs[1:]:
            dy = dy * xval + y
            y = y * xval + c
        return y, dy

    # Upper bound evaluation from a 3-vector c (normalized internally)
    # UB = r_max^2 / (2*pi), where r_max is the largest positive real root of Q(x)=P(x)/x^2
    def upper_bound_from_coeffs(c: np.ndarray) -> float:
        nrm = float(np.linalg.norm(c))
        if not np.isfinite(nrm) or nrm == 0.0:
            return float('inf')
        c = c / nrm
        c0, c1, c2 = float(c[0]), float(c[1]), float(c[2])

        # Build P coefficients and then Q coefficients by dividing by x^2 (drop x^0 and x^1)
        P = build_P_coeffs(c0, c1, c2)
        # Guard: lower two terms are x^1 and x^0 and should be zero for exact construction
        P[-1] = 0.0
        P[-2] = 0.0

        # Q has coefficients for degrees 12..2 (length 11), contiguous in degree
        Q = P[:11]

        # Leading coefficient guard
        if not np.isfinite(Q[0]) or abs(Q[0]) < 1e-16:
            return float('inf')

        # Compute roots of Q
        try:
            roots = np.roots(Q)
        except Exception:
            return float('inf')

        # Filter to real positive roots (small imag tolerated)
        imag_small = np.isclose(roots.imag, 0.0, atol=1e-8)
        real_roots = roots.real[imag_small]
        pos_roots = real_roots[real_roots > 1e-10]
        if pos_roots.size == 0:
            # No positive real roots, penalize
            return 1e9

        # Newton-polish each candidate root slightly to improve accuracy
        # Note: Q is given by descending coefficients Q (degrees 12..2).
        def polish(r: float) -> float:
            xk = float(r)
            # Limit the number of iterations and ensure we stay in positive domain
            for _ in range(3):
                val, der = eval_poly_and_der_desc(Q, xk)
                if not np.isfinite(val) or not np.isfinite(der) or der == 0.0:
                    break
                step = val / der
                xk -= step
                if xk <= 1e-12 or not np.isfinite(xk):
                    # If Newton overshoots or becomes invalid, revert to previous r
                    return r
            return xk if xk > 1e-12 and np.isfinite(xk) else r

        if pos_roots.size > 0:
            polished = np.array([polish(r) for r in pos_roots], dtype=float)
            # Validate by checking their imaginary resid (re-evaluation)
            # and clamping any regressions
            polished = polished[np.isfinite(polished)]
            polished = polished[polished > 1e-9] if polished.size else pos_roots
            if polished.size > 0:
                r_max = float(np.max(polished))
            else:
                r_max = float(np.max(pos_roots))
        else:
            r_max = float(np.max(pos_roots))

        # UB
        return (r_max * r_max) / (2.0 * math.pi)

    # A faster evaluation for 1D seeding: fix c2 and c1, choose c0 to make partial P(0)=0 (ignoring H12),
    # which implies c3 ≈ 0 when the verifier appends H12. Then evaluate exact UB with the appended H12.
    def ub_from_c1_c2(c1: float, c2: float) -> Tuple[float, np.ndarray]:
        # Choose c0 so that c0*h0(0) + c1*h4(0) + c2*h8(0) = 0 => c0 = -(h4_0*c1 + h8_0*c2)/h0_0
        c0 = - (h4_0 * c1 + h8_0 * c2) / h0_0
        v = np.array([c0, c1, c2], dtype=float)
        # Normalize to unit sphere to respect scale invariance
        nrm = np.linalg.norm(v)
        if nrm == 0.0:
            return float('inf'), v
        v = v / nrm
        return upper_bound_from_coeffs(v), v

    # Local refinement on the sphere via random Gaussian steps with decreasing sigma
    def local_refine(c_best: np.ndarray, ub_best: float, rounds: int = 4, iters_per_round: int = 200) -> Tuple[np.ndarray, float]:
        c = c_best.copy()
        ub = float(ub_best)
        sigma = 0.25
        for _ in range(rounds):
            for _ in range(iters_per_round):
                step = np.random.normal(0.0, sigma, size=3)
                cand = c + step
                nrm = np.linalg.norm(cand)
                if nrm == 0.0:
                    continue
                cand /= nrm
                ub_cand = upper_bound_from_coeffs(cand)
                if ub_cand < ub:
                    c, ub = cand, ub_cand
            sigma *= 0.5
        return c, ub

    # Additional simple Nelder–Mead on the sphere's local tangent plane
    def nelder_mead_sphere(c_start: np.ndarray, eval_fn, max_iters: int = 120) -> Tuple[np.ndarray, float]:
        # Build an initial simplex around c_start with small perturbations and re-normalize
        scale = 0.12
        simplex = [c_start.copy()]
        for _ in range(3):  # 3 more points to form a tetrahedron projected to sphere
            p = c_start + np.random.normal(0.0, scale, size=3)
            p /= np.linalg.norm(p)
            simplex.append(p)
        vals = [eval_fn(p) for p in simplex]

        for _ in range(max_iters):
            # Order
            idx = np.argsort(vals)
            simplex = [simplex[i] for i in idx]
            vals = [vals[i] for i in idx]
            best, worst, second_worst = simplex[0], simplex[-1], simplex[-2]
            f_best, f_worst, f_second = vals[0], vals[-1], vals[-2]

            # Centroid of all but worst
            centroid = np.mean(simplex[:-1], axis=0)
            centroid /= np.linalg.norm(centroid)

            # Reflection
            alpha = 1.0
            xr = centroid + alpha * (centroid - worst)
            xr /= np.linalg.norm(xr)
            fr = eval_fn(xr)

            if fr < f_best:
                # Expansion
                gamma = 1.8
                xe = centroid + gamma * (xr - centroid)
                xe /= np.linalg.norm(xe)
                fe = eval_fn(xe)
                simplex[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
            elif fr < f_second:
                # Accept reflection
                simplex[-1], vals[-1] = xr, fr
            else:
                # Contraction
                rho = 0.5
                if fr < f_worst:
                    # Outside contraction
                    xc = centroid + rho * (xr - centroid)
                else:
                    # Inside contraction
                    xc = centroid - rho * (centroid - worst)
                xc /= np.linalg.norm(xc)
                fc = eval_fn(xc)
                if fc < f_worst:
                    simplex[-1], vals[-1] = xc, fc
                else:
                    # Shrink towards best
                    sigma_nm = 0.5
                    for i in range(1, len(simplex)):
                        simplex[i] = best + sigma_nm * (simplex[i] - best)
                        simplex[i] /= np.linalg.norm(simplex[i])
                        vals[i] = eval_fn(simplex[i])
        # Return best
        idx = int(np.argmin(vals))
        return simplex[idx], float(vals[idx])

    # Differential-Evolution-like global exploration on the sphere
    def differential_evolution(population: List[np.ndarray], max_gens: int = 90) -> Tuple[np.ndarray, float]:
        pop_size = len(population)
        F_mutation = 0.75
        CR = 0.9

        ub_values = np.array([upper_bound_from_coeffs(ind) for ind in population], dtype=float)
        best_idx = int(np.argmin(ub_values))
        best_vec = population[best_idx].copy()
        best_ub = float(ub_values[best_idx])

        # Main loop
        for _ in range(max_gens):
            for i in range(pop_size):
                # Choose 3 distinct indices different from i
                idxs = list(range(pop_size))
                idxs.remove(i)
                a, b, cidx = rng.sample(idxs, 3)
                xa, xb, xc = population[a], population[b], population[cidx]
                mutant = xa + F_mutation * (xb - xc)
                nrm = np.linalg.norm(mutant)
                if nrm == 0.0 or not np.isfinite(nrm):
                    mutant = xa.copy()
                else:
                    mutant /= nrm
                # Binomial crossover
                trial = population[i].copy()
                jrand = rng.randrange(3)
                for j in range(3):
                    if rng.random() < CR or j == jrand:
                        trial[j] = mutant[j]
                # Re-normalize to sphere
                trial /= np.linalg.norm(trial)
                # Evaluate
                ub_trial = upper_bound_from_coeffs(trial)
                if ub_trial < ub_values[i]:
                    population[i] = trial
                    ub_values[i] = ub_trial
                    if ub_trial < best_ub:
                        best_vec = trial.copy()
                        best_ub = ub_trial
            # Occasional local polish on the current best to accelerate convergence
            best_vec, best_ub = local_refine(best_vec, best_ub, rounds=1, iters_per_round=140)
        return best_vec, best_ub

    # 1D golden section search helper for seeding in c1 given fixed c2
    def golden_section_search_c1(c2: float, a: float, b: float, iters: int = 24) -> Tuple[float, np.ndarray]:
        invphi = (math.sqrt(5) - 1) / 2
        invphi2 = (3 - math.sqrt(5)) / 2
        # initial interior points
        h = b - a
        c = a + invphi2 * h
        d = a + invphi * h
        fc, vc = ub_from_c1_c2(c, c2)
        fd, vd = ub_from_c1_c2(d, c2)
        for _ in range(iters):
            if fc < fd:
                b, d, fd = d, c, fc
                c = a + invphi2 * (b - a)
                fc, vc = ub_from_c1_c2(c, c2)
            else:
                a, c, fc = c, d, fd
                d = a + invphi * (b - a)
                fd, vd = ub_from_c1_c2(d, c2)
        if fc < fd:
            return fc, vc
        else:
            return fd, vd

    # Build seeds
    seed_vectors: List[np.ndarray] = []

    # Heuristic seeds: negative c1, positive c2, moderate c0
    heuristic = [
        np.array([1.0, -0.8, 0.3], dtype=float),
        np.array([1.0, -1.2, 0.35], dtype=float),
        np.array([0.8, -1.5, 0.5], dtype=float),
        np.array([1.0, -0.6, 0.2], dtype=float),
        np.array([0.6, -1.8, 0.7], dtype=float),
        np.array([0.9, -1.0, 0.4], dtype=float),
        np.array([1.0, -1.6, 0.6], dtype=float),
        np.array([1.0, -2.0, 0.9], dtype=float),
        np.array([0.7, -1.3, 0.55], dtype=float),
        np.array([0.5, -1.1, 0.25], dtype=float),
        np.array([0.9, -2.2, 0.8], dtype=float),
        np.array([0.7, -2.5, 0.95], dtype=float),
    ]
    for s in heuristic:
        s = s / np.linalg.norm(s)
        seed_vectors.append(s)

    # 1D scans over c1 for multiple c2 values
    c2_values = [0.22, 0.25, 0.3, 0.33, 0.37, 0.4, 0.45, 0.48, 0.52, 0.55, 0.62, 0.7, 0.8, 0.9, 1.0]
    # Coarse grid + golden-section for good seeds
    for c2 in c2_values:
        # Coarse grid for c1 in a wide negative range
        c1_grid = np.linspace(-6.0, -0.1, 90)
        best_ub = float('inf')
        best_vec = None
        best_idx = 0
        for idx, c1 in enumerate(c1_grid):
            ub, vec = ub_from_c1_c2(c1, c2)
            if ub < best_ub and np.isfinite(ub):
                best_ub = ub
                best_vec = vec
                best_idx = idx
        # Use neighborhood of the best c1 to refine with golden-section
        if best_vec is not None:
            # Build local bracket around best grid point
            left = c1_grid[max(0, best_idx - 2)]
            right = c1_grid[min(len(c1_grid) - 1, best_idx + 2)]
            # Ensure valid bracket length
            if right - left < 1e-4:
                left, right = -6.0, -0.1
            ub_g, vec_g = golden_section_search_c1(c2, left, right, iters=28)
            seed_vectors.append(vec_g)

    # Random unit seeds for diversity
    def random_unit_vec() -> np.ndarray:
        while True:
            v = np.array([rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1)], dtype=float)
            nrm = np.linalg.norm(v)
            if nrm > 0.0:
                return v / nrm

    for _ in range(80):
        seed_vectors.append(random_unit_vec())

    # Deduplicate seeds and cap total seeds
    seed_vectors = [v / np.linalg.norm(v) for v in seed_vectors]
    uniq = {}
    for v in seed_vectors:
        key = tuple(np.round(v, 6))
        if key not in uniq:
            uniq[key] = v
    seed_vectors = list(uniq.values())

    # Evaluate seeds and pick top-N
    seed_vals = [(upper_bound_from_coeffs(v), v) for v in seed_vectors]
    seed_vals = [(ub, v) for ub, v in seed_vals if np.isfinite(ub)]
    seed_vals.sort(key=lambda t: t[0])
    # Keep top K seeds
    K = min(140, len(seed_vals))
    population = [seed_vals[i][1] for i in range(K)]

    # Early polish of the single best seed
    best_vec = population[0].copy()
    best_ub = upper_bound_from_coeffs(best_vec)
    best_vec, best_ub = local_refine(best_vec, best_ub, rounds=2, iters_per_round=260)

    # Run DE
    best_vec_de, best_ub_de = differential_evolution(population, max_gens=100)
    if best_ub_de < best_ub:
        best_vec, best_ub = best_vec_de, best_ub_de

    # Nelder–Mead polish
    best_vec, best_ub = nelder_mead_sphere(best_vec, upper_bound_from_coeffs, max_iters=160)

    # Final local refinement pass
    best_vec, best_ub = local_refine(best_vec, best_ub, rounds=3, iters_per_round=320)

    # If still not satisfactory, perform a last extended NM around best
    if not np.isfinite(best_ub) or best_ub > 0.35212:
        best_vec, best_ub = nelder_mead_sphere(best_vec, upper_bound_from_coeffs, max_iters=220)
        best_vec, best_ub = local_refine(best_vec, best_ub, rounds=2, iters_per_round=400)

    # Rationalization: approximate to rationals with bounded denominators while preserving UB
    def rationalize_vector(vec: np.ndarray) -> List[str]:
        v = vec / np.linalg.norm(vec)
        candidates = []
        # Try multiple denominator limits and accept the best that doesn't hurt UB
        # We progressively allow larger denominators to capture fine ratios.
        for max_denom in [40, 80, 120, 200, 400, 800, 1200]:
            try:
                r0 = sympy.nsimplify(float(v[0]), rational=True, maxsteps=60, maxden=max_denom)
                r1 = sympy.nsimplify(float(v[1]), rational=True, maxsteps=60, maxden=max_denom)
                r2 = sympy.nsimplify(float(v[2]), rational=True, maxsteps=60, maxden=max_denom)
                rat_vec = np.array([float(r0), float(r1), float(r2)], dtype=float)
                rat_vec /= np.linalg.norm(rat_vec)
                ub_rat = upper_bound_from_coeffs(rat_vec)
                candidates.append((ub_rat, [str(r0), str(r1), str(r2)], rat_vec))
                # Early break if essentially no loss
                if ub_rat <= best_ub + 2e-5:
                    break
            except Exception:
                continue
        if candidates:
            candidates.sort(key=lambda t: t[0])
            return candidates[0][1]
        # Fallback: decimal strings (SymPy can parse, but rationals preferred)
        return [f"{v[0]:.12g}", f"{v[1]:.12g}", f"{v[2]:.12g}"]

    coeffs = rationalize_vector(best_vec)

    return coeffs
# EVOLVE_END


if __name__ == "__main__":
    print(json.dumps({"coefficients": find_coefficients()}))