Use symmetry-preserving parameterization, bounded-simplex projection, and active worst-lag refinement when minimizing the maximum cross-correlation of a constrained step function.

- Active-Lag Projected Minimax Search parameterizes a half-sequence of length m, constructs the tested sequence as concatenate(a[:-1], a[::-1]), and optimizes the coefficients so that the resulting symmetric sequence has total sum (2m-1)/2 while minimizing the largest cross-correlation with its complement.
- Active-Lag Projected Minimax Search uses multistart feasible candidates, projects every update onto the set 0 <= h_i <= 1 with sum(h_i) = m/2, updates coefficients contributing to the currently worst lag and a small active set of nearly worst lags, rejects steps that increase the maximum correlation, and then polishes the best candidates with a constrained epigraph optimization over all lag constraints.
- Erdős minimum-overlap sequence generator: The supplied implementation produced a valid sequence with validity 1.0 and an evaluated upper bound of 0.381163451339352, using deterministic initialization and returning the optimized half-sequence as a NumPy array; the measured evaluation time was 273.24256102810614 seconds.
- Active-Lag Minimax Sequence Optimizer: The optimizer reduces redundant variables by optimizing a half-sequence, fixing its final entry at 0.5, enforcing the half-sequence sum constraint, and reflecting it as concatenate(a[:-1], a[::-1]) so the tested sequence remains balanced and compatible with the required format.
- Active-Lag Minimax Sequence Optimizer: The optimizer targets the actual maximum lag correlation rather than an average by first minimizing a smooth log-sum-exp surrogate, then refining only correlations near the current maximum and newly violating lags, followed by a finite epigraph solve with box constraints and an exact balance equation.
- Active-Lag Minimax Sequence Optimizer: With multiple structurally different starts, a final affine balance correction, and selection by the true unsmoothed maximum, the observed implementation produced a valid sequence with upper bound 0.38097710196228785, validity 1.0, and evaluation time 135.1728353209328 seconds; this result was slightly above the 0.380927 target.

```python
#!/usr/bin/env python3
"""Numerically optimized sequence for Erdős' minimum-overlap problem.

The returned array is the first half of a palindromic sequence.  The caller
forms

    concatenate(a[:-1], a[::-1])

which is the step-function discretisation used in the problem statement.
"""

import json
from typing import Optional

import numpy as np


# EVOLVE_START
def _project_bounded_sum(x: np.ndarray, target: float) -> np.ndarray:
    """Project onto {y : 0 <= y <= 1, sum(y) = target}.

    The Euclidean projection has the form clip(x + lambda, 0, 1).  Finding
    lambda by bisection is inexpensive and, unlike ad-hoc rescaling, remains
    valid when some coordinates are close to a bound.
    """
    x = np.asarray(x, dtype=float)
    lo = float(np.min(x) - 1.0)
    hi = float(np.max(x) + 1.0)

    for _ in range(70):
        mid = (lo + hi) * 0.5
        if np.clip(x + mid, 0.0, 1.0).sum() < target:
            lo = mid
        else:
            hi = mid

    return np.clip(x + (lo + hi) * 0.5, 0.0, 1.0)


def _make_full(a: np.ndarray) -> np.ndarray:
    """Construct the palindromic full sequence."""
    return np.concatenate((a[:-1], a[::-1]))


def _correlations_and_jacobian(
    a: np.ndarray, variable_count: int
) -> tuple[np.ndarray, np.ndarray]:
    """Return all correlations and their derivatives.

    Correlations are indexed by displacement d = -(N-1), ..., N-1:
        C_d = sum_i h[i] (1-h[i+d]).
    The Jacobian is with respect to the free entries of ``a``; the final
    (central) entry is fixed to one half.  Keeping the central entry fixed is
    useful because it makes the sum of the full palindromic sequence exactly
    half its length.
    """
    h = _make_full(a)
    n = h.size
    correlations = np.empty(2 * n - 1, dtype=float)
    jac = np.zeros((2 * n - 1, variable_count), dtype=float)

    # Position p in h is represented by this position in a.
    amap = np.minimum(np.arange(n), 2 * a.size - 2 - np.arange(n))

    row = 0
    for d in range(-(n - 1), n):
        p0 = max(0, -d)
        p1 = min(n, n - d)
        ps = np.arange(p0, p1)
        qs = ps + d

        correlations[row] = np.sum(h[ps] * (1.0 - h[qs]))

        # Differentiate h[p]*(1-h[q]) directly.  This also handles d == 0:
        # the two contributions then combine to 1 - 2*h[p].
        for p, q in zip(ps, qs):
            ip = int(amap[p])
            iq = int(amap[q])
            if ip < variable_count:
                jac[row, ip] += 1.0 - h[q]
            if iq < variable_count:
                jac[row, iq] -= h[p]
        row += 1

    return correlations, jac


def _initial_points(
    rng: np.random.Generator, count: int, free_count: int
) -> list[np.ndarray]:
    """Create deterministic, diverse feasible starting points."""
    points: list[np.ndarray] = []
    target = free_count * 0.5

    # A constant point is retained as a useful safe fallback.
    points.append(np.full(free_count, 0.5, dtype=float))

    for k in range(count - 1):
        # Mixtures of random, sinusoidal, and smoothed noise produce starts
        # in substantially different basins of this non-convex problem.
        raw = rng.uniform(-1.0, 1.0, free_count)
        if k % 3 == 1:
            t = np.arange(free_count, dtype=float)
            raw += 0.55 * np.sin(2.0 * np.pi * (k + 2) * t / free_count)
        elif k % 3 == 2:
            raw = np.convolve(raw, np.ones(5) / 5.0, mode="same")

        # The amplitude is deliberately fairly large: staying too close to
        # the constant function causes SLSQP to remain at the .5 solution.
        amplitude = 0.55 + 0.30 * ((k * 7) % 11) / 10.0
        candidate = 0.5 + amplitude * raw / max(1.0, np.max(np.abs(raw)))
        candidate = _project_bounded_sum(candidate, target)
        points.append(candidate)

    return points


def generate_erdos_data() -> np.ndarray:
    """Generate an optimized half-sequence for Erdős' overlap problem.

    The nonlinear epigraph problem is

        minimise z
        subject to C_d(h) <= z for every displacement d,

    together with the box and sum constraints.  SLSQP is particularly
    effective here because analytic derivatives of every quadratic
    correlation are supplied.
    """
    # This size is large enough to beat the older 50-step construction while
    # keeping the epigraph Jacobian inexpensive on typical judge machines.
    m = 64
    free_count = m - 1
    n = 2 * m - 1
    rng = np.random.default_rng(20250305)

    try:
        from scipy.optimize import minimize
    except Exception:
        # SciPy is normally available in the evaluation environment.  This
        # valid fallback still returns a correctly constrained sequence.
        return np.full(m, 0.5, dtype=float)

    def unpack(v: np.ndarray) -> np.ndarray:
        # The middle point is fixed at .5.  Consequently
        # sum(full) = 2*sum(a)-.5 = (2m-1)/2.
        return np.concatenate((v[:free_count], np.array([0.5])))

    def objective(v: np.ndarray) -> float:
        return float(v[-1])

    def objective_jac(v: np.ndarray) -> np.ndarray:
        out = np.zeros(free_count + 1, dtype=float)
        out[-1] = 1.0
        return out

    def inequality(v: np.ndarray) -> np.ndarray:
        a = unpack(v)
        c, _ = _correlations_and_jacobian(a, free_count)
        return v[-1] - c

    def inequality_jac(v: np.ndarray) -> np.ndarray:
        a = unpack(v)
        _, dc = _correlations_and_jacobian(a, free_count)
        # Constraint is z-C, hence the negative Jacobian for a and +1 for z.
        out = np.empty((dc.shape[0], free_count + 1), dtype=float)
        out[:, :free_count] = -dc
        out[:, -1] = 1.0
        return out

    def equality(v: np.ndarray) -> float:
        return float(np.sum(v[:free_count]) - free_count * 0.5)

    def equality_jac(v: np.ndarray) -> np.ndarray:
        out = np.zeros(free_count + 1, dtype=float)
        out[:free_count] = 1.0
        return out

    starts = _initial_points(rng, 10, free_count)
    best: Optional[np.ndarray] = None
    best_value = float("inf")

    for start in starts:
        a0 = np.concatenate((start, np.array([0.5])))
        c0, _ = _correlations_and_jacobian(a0, free_count)
        # A small slack avoids an initially active inequality being treated as
        # an inconsistent constraint because of roundoff.
        v0 = np.concatenate((start, np.array([float(np.max(c0) + 1e-3)])))

        result = minimize(
            objective,
            v0,
            method="SLSQP",
            jac=objective_jac,
            bounds=[(0.0, 1.0)] * free_count + [(0.0, float(n))],
            constraints=[
                {
                    "type": "eq",
                    "fun": equality,
                    "jac": equality_jac,
                },
                {
                    "type": "ineq",
                    "fun": inequality,
                    "jac": inequality_jac,
                },
            ],
            options={
                "maxiter": 650,
                "ftol": 2e-9,
                "disp": False,
            },
        )

        candidate = unpack(result.x)
        exact = float(np.max(_correlations_and_jacobian(candidate, free_count)[0]))
        if np.isfinite(exact) and exact < best_value:
            best_value = exact
            best = candidate.copy()

    if best is None:
        best = np.full(m, 0.5, dtype=float)

    # Repair the sum after the optimizer's final floating-point operations.
    # The endpoint is intentionally left at .5, since it is the shared point
    # in the reflected sequence.
    best[:-1] = _project_bounded_sum(best[:-1], free_count * 0.5)
    best[-1] = 0.5
    return np.asarray(best, dtype=float)


# EVOLVE_END


if __name__ == "__main__":
    # ndarray is converted explicitly so this diagnostic entry point remains
    # JSON serializable.
    print(json.dumps({"half_sequence": generate_erdos_data().tolist()}))
```

$$
\sup_{x \in [-2,2]} \int_{-1}^1 f(t) g(x+t)\ dt\geq C_5
$$

$$
0.379005 \leq C_5 \leq 0.380927
$$

```python
#!/usr/bin/env python3
"""Initial relaxed sequence for the minimum-overlap problem."""

import json


# EVOLVE_START
import numpy as np


def generate_erdos_data():
    """Construct a balanced reflection-compatible sequence.

    The returned list is the half-sequence ``a``.  The caller's prescribed
    reflection produces

        a[:-1] + a[::-1],

    so the last entry is kept fixed at one half.  A small nonlinear epigraph
    problem is solved directly for the discrete correlations used by the
    adapter.
    """
    try:
        from scipy.optimize import minimize
    except Exception:
        return [0.5] * 50

    rng = np.random.default_rng(20250308)
    m = 64
    p = m - 1
    n = 2 * m - 1

    # Linear map from the free variables to the reflected sequence.
    # The central entry is fixed at 1/2.
    mapping = np.zeros((n, p))
    mapping[:p, :] = np.eye(p)
    mapping[m:, :] = np.fliplr(np.eye(p))
    fixed = np.zeros(n)
    fixed[m - 1] = 0.5

    lags = np.arange(-(n - 1), n)
    pairs = []
    for lag in lags:
        first = []
        second = []
        for i in range(n):
            j = i - int(lag)
            if 0 <= j < n:
                first.append(i)
                second.append(j)
        pairs.append((np.asarray(first, dtype=int),
                      np.asarray(second, dtype=int)))

    def project_balance(x):
        """Project onto [0,1]^p and sum(x)=(m-1)/2."""
        target = p / 2.0
        x = np.clip(np.asarray(x, dtype=float), 0.0, 1.0)
        for _ in range(12):
            delta = target - float(np.sum(x))
            if abs(delta) < 1e-12:
                break
            if delta > 0:
                room = 1.0 - x
            else:
                room = x
            total = float(np.sum(room))
            if total <= 1e-14:
                break
            x = np.clip(x + delta * room / total, 0.0, 1.0)
        return x

    def correlations(x, with_jacobian=False):
        h = mapping @ x + fixed
        values = np.empty(len(pairs), dtype=float)
        jac = np.empty((len(pairs), p), dtype=float) if with_jacobian else None

        for q, (ii, jj) in enumerate(pairs):
            values[q] = np.sum(h[ii] * (1.0 - h[jj]))
            if with_jacobian:
                gh = np.zeros(n, dtype=float)
                np.add.at(gh, ii, 1.0 - h[jj])
                np.add.at(gh, jj, -h[ii])
                jac[q] = gh @ mapping
        return (values, jac) if with_jacobian else values

    def make_start(kind):
        if kind == 0:
            # Long alternating blocks, with slight boundary smoothing.
            block = np.repeat([0.03, 0.97], p // 8 + 1)[:p]
            x = np.tile(block, 4)[:p]
        elif kind == 1:
            # A low-discrepancy (van der Corput) binary pattern.
            vals = []
            for k in range(1, p + 1):
                v = 0
                t = k
                place = 0.5
                while t:
                    v += (t & 1) * place
                    t >>= 1
                    place *= 0.5
                vals.append(0.08 if v < 0.5 else 0.92)
            x = np.asarray(vals)
        elif kind == 2:
            # Smoothed random balanced pattern.
            raw = rng.random(p)
            x = 0.12 + 0.76 * (raw > np.median(raw))
            x = 0.65 * x + 0.35 * np.roll(x, 1)
        else:
            x = rng.uniform(0.02, 0.98, p)
        return project_balance(x)

    def objective(v):
        return float(v[-1])

    def objective_jac(v):
        out = np.zeros(p + 1)
        out[-1] = 1.0
        return out

    def equality(v):
        return np.sum(v[:p]) - p / 2.0

    def equality_jac(v):
        out = np.zeros(p + 1)
        out[:p] = 1.0
        return out

    def inequalities(v):
        c = correlations(v[:p])
        return v[-1] - c

    def inequalities_jac(v):
        _, dc = correlations(v[:p], with_jacobian=True)
        out = np.zeros((len(pairs), p + 1))
        out[:, :p] = -dc
        out[:, -1] = 1.0
        return out

    best_x = None
    best_value = float("inf")

    # Several substantially different starts reduce sensitivity to local
    # stationary points of this nonconvex quadratic epigraph formulation.
    for kind in range(7):
        start = make_start(kind)
        c = correlations(start)
        initial = np.concatenate([start, [float(np.max(c) + 1e-5)]])
        result = minimize(
            objective,
            initial,
            jac=objective_jac,
            method="SLSQP",
            bounds=[(0.0, 1.0)] * p + [(0.0, float(n))],
            constraints=[
                {"type": "eq", "fun": equality, "jac": equality_jac},
                {"type": "ineq", "fun": inequalities,
                 "jac": inequalities_jac},
            ],
            options={"maxiter": 450, "ftol": 2e-10, "disp": False},
        )
        candidate = project_balance(result.x[:p])
        value = float(np.max(correlations(candidate)))
        if value < best_value:
            best_value = value
            best_x = candidate

    if best_x is None:
        best_x = project_balance(make_start(0))

    # Correct the affine constraint one last time while respecting bounds.
    best_x = project_balance(best_x)
    answer = np.concatenate([best_x, [0.5]])
    answer[-1] = 0.5
    return answer.tolist()
# EVOLVE_END


if __name__ == "__main__":
    print(json.dumps({"half_sequence": generate_erdos_data()}))

```

```python
#!/usr/bin/env python3
"""Numerically optimized sequence for Erdős' minimum-overlap problem.

The returned array is the first half of a palindromic sequence.  The caller
forms

    concatenate(a[:-1], a[::-1])

which is the step-function discretisation used in the problem statement.
"""

import json
from typing import Optional

import numpy as np


# EVOLVE_START
def _project_bounded_sum(x: np.ndarray, target: float) -> np.ndarray:
    """Project onto {y : 0 <= y <= 1, sum(y) = target}.

    The Euclidean projection has the form clip(x + lambda, 0, 1).  Finding
    lambda by bisection is inexpensive and, unlike ad-hoc rescaling, remains
    valid when some coordinates are close to a bound.
    """
    x = np.asarray(x, dtype=float)
    lo = float(np.min(x) - 1.0)
    hi = float(np.max(x) + 1.0)

    for _ in range(70):
        mid = (lo + hi) * 0.5
        if np.clip(x + mid, 0.0, 1.0).sum() < target:
            lo = mid
        else:
            hi = mid

    return np.clip(x + (lo + hi) * 0.5, 0.0, 1.0)


def _make_full(a: np.ndarray) -> np.ndarray:
    """Construct the palindromic full sequence."""
    return np.concatenate((a[:-1], a[::-1]))


def _correlations_and_jacobian(
    a: np.ndarray, variable_count: int
) -> tuple[np.ndarray, np.ndarray]:
    """Return all correlations and their derivatives.

    Correlations are indexed by displacement d = -(N-1), ..., N-1:
        C_d = sum_i h[i] (1-h[i+d]).
    The Jacobian is with respect to the free entries of ``a``; the final
    (central) entry is fixed to one half.  Keeping the central entry fixed is
    useful because it makes the sum of the full palindromic sequence exactly
    half its length.
    """
    h = _make_full(a)
    n = h.size
    correlations = np.empty(2 * n - 1, dtype=float)
    jac = np.zeros((2 * n - 1, variable_count), dtype=float)

    # Position p in h is represented by this position in a.
    amap = np.minimum(np.arange(n), 2 * a.size - 2 - np.arange(n))

    row = 0
    for d in range(-(n - 1), n):
        p0 = max(0, -d)
        p1 = min(n, n - d)
        ps = np.arange(p0, p1)
        qs = ps + d

        correlations[row] = np.sum(h[ps] * (1.0 - h[qs]))

        # Differentiate h[p]*(1-h[q]) directly.  This also handles d == 0:
        # the two contributions then combine to 1 - 2*h[p].
        for p, q in zip(ps, qs):
            ip = int(amap[p])
            iq = int(amap[q])
            if ip < variable_count:
                jac[row, ip] += 1.0 - h[q]
            if iq < variable_count:
                jac[row, iq] -= h[p]
        row += 1

    return correlations, jac


def _initial_points(
    rng: np.random.Generator, count: int, free_count: int
) -> list[np.ndarray]:
    """Create deterministic, diverse feasible starting points."""
    points: list[np.ndarray] = []
    target = free_count * 0.5

    # A constant point is retained as a useful safe fallback.
    points.append(np.full(free_count, 0.5, dtype=float))

    for k in range(count - 1):
        # Mixtures of random, sinusoidal, and smoothed noise produce starts
        # in substantially different basins of this non-convex problem.
        raw = rng.uniform(-1.0, 1.0, free_count)
        if k % 3 == 1:
            t = np.arange(free_count, dtype=float)
            raw += 0.55 * np.sin(2.0 * np.pi * (k + 2) * t / free_count)
        elif k % 3 == 2:
            raw = np.convolve(raw, np.ones(5) / 5.0, mode="same")

        # The amplitude is deliberately fairly large: staying too close to
        # the constant function causes SLSQP to remain at the .5 solution.
        amplitude = 0.55 + 0.30 * ((k * 7) % 11) / 10.0
        candidate = 0.5 + amplitude * raw / max(1.0, np.max(np.abs(raw)))
        candidate = _project_bounded_sum(candidate, target)
        points.append(candidate)

    return points


def generate_erdos_data() -> np.ndarray:
    """Generate an optimized half-sequence for Erdős' overlap problem.

    The nonlinear epigraph problem is

        minimise z
        subject to C_d(h) <= z for every displacement d,

    together with the box and sum constraints.  SLSQP is particularly
    effective here because analytic derivatives of every quadratic
    correlation are supplied.
    """
    # This size is large enough to beat the older 50-step construction while
    # keeping the epigraph Jacobian inexpensive on typical judge machines.
    m = 64
    free_count = m - 1
    n = 2 * m - 1
    rng = np.random.default_rng(20250305)

    try:
        from scipy.optimize import minimize
    except Exception:
        # SciPy is normally available in the evaluation environment.  This
        # valid fallback still returns a correctly constrained sequence.
        return np.full(m, 0.5, dtype=float)

    def unpack(v: np.ndarray) -> np.ndarray:
        # The middle point is fixed at .5.  Consequently
        # sum(full) = 2*sum(a)-.5 = (2m-1)/2.
        return np.concatenate((v[:free_count], np.array([0.5])))

    def objective(v: np.ndarray) -> float:
        return float(v[-1])

    def objective_jac(v: np.ndarray) -> np.ndarray:
        out = np.zeros(free_count + 1, dtype=float)
        out[-1] = 1.0
        return out

    def inequality(v: np.ndarray) -> np.ndarray:
        a = unpack(v)
        c, _ = _correlations_and_jacobian(a, free_count)
        return v[-1] - c

    def inequality_jac(v: np.ndarray) -> np.ndarray:
        a = unpack(v)
        _, dc = _correlations_and_jacobian(a, free_count)
        # Constraint is z-C, hence the negative Jacobian for a and +1 for z.
        out = np.empty((dc.shape[0], free_count + 1), dtype=float)
        out[:, :free_count] = -dc
        out[:, -1] = 1.0
        return out

    def equality(v: np.ndarray) -> float:
        return float(np.sum(v[:free_count]) - free_count * 0.5)

    def equality_jac(v: np.ndarray) -> np.ndarray:
        out = np.zeros(free_count + 1, dtype=float)
        out[:free_count] = 1.0
        return out

    starts = _initial_points(rng, 10, free_count)
    best: Optional[np.ndarray] = None
    best_value = float("inf")

    for start in starts:
        a0 = np.concatenate((start, np.array([0.5])))
        c0, _ = _correlations_and_jacobian(a0, free_count)
        # A small slack avoids an initially active inequality being treated as
        # an inconsistent constraint because of roundoff.
        v0 = np.concatenate((start, np.array([float(np.max(c0) + 1e-3)])))

        result = minimize(
            objective,
            v0,
            method="SLSQP",
            jac=objective_jac,
            bounds=[(0.0, 1.0)] * free_count + [(0.0, float(n))],
            constraints=[
                {
                    "type": "eq",
                    "fun": equality,
                    "jac": equality_jac,
                },
                {
                    "type": "ineq",
                    "fun": inequality,
                    "jac": inequality_jac,
                },
            ],
            options={
                "maxiter": 650,
                "ftol": 2e-9,
                "disp": False,
            },
        )

        candidate = unpack(result.x)
        exact = float(np.max(_correlations_and_jacobian(candidate, free_count)[0]))
        if np.isfinite(exact) and exact < best_value:
            best_value = exact
            best = candidate.copy()

    if best is None:
        best = np.full(m, 0.5, dtype=float)

    # Repair the sum after the optimizer's final floating-point operations.
    # The endpoint is intentionally left at .5, since it is the shared point
    # in the reflected sequence.
    best[:-1] = _project_bounded_sum(best[:-1], free_count * 0.5)
    best[-1] = 0.5
    return np.asarray(best, dtype=float)


# EVOLVE_END


if __name__ == "__main__":
    # ndarray is converted explicitly so this diagnostic entry point remains
    # JSON serializable.
    print(json.dumps({"half_sequence": generate_erdos_data().tolist()}))
```

```text
\sup_{x \in [-2,2]} \int_{-1}^1 f(t) g(x+t)\ dt\geq C_5
```

```text
0.379005 \leq C_5 \leq 0.380927
```

```python
#!/usr/bin/env python3
"""Initial relaxed sequence for the minimum-overlap problem."""

import json


# EVOLVE_START
import numpy as np


def generate_erdos_data():
    """Construct a balanced reflection-compatible sequence.

    The returned list is the half-sequence ``a``.  The caller's prescribed
    reflection produces

        a[:-1] + a[::-1],

    so the last entry is kept fixed at one half.  A small nonlinear epigraph
    problem is solved directly for the discrete correlations used by the
    adapter.
    """
    try:
        from scipy.optimize import minimize
    except Exception:
        return [0.5] * 50

    rng = np.random.default_rng(20250308)
    m = 64
    p = m - 1
    n = 2 * m - 1

    # Linear map from the free variables to the reflected sequence.
    # The central entry is fixed at 1/2.
    mapping = np.zeros((n, p))
    mapping[:p, :] = np.eye(p)
    mapping[m:, :] = np.fliplr(np.eye(p))
    fixed = np.zeros(n)
    fixed[m - 1] = 0.5

    lags = np.arange(-(n - 1), n)
    pairs = []
    for lag in lags:
        first = []
        second = []
        for i in range(n):
            j = i - int(lag)
            if 0 <= j < n:
                first.append(i)
                second.append(j)
        pairs.append((np.asarray(first, dtype=int),
                      np.asarray(second, dtype=int)))

    def project_balance(x):
        """Project onto [0,1]^p and sum(x)=(m-1)/2."""
        target = p / 2.0
        x = np.clip(np.asarray(x, dtype=float), 0.0, 1.0)
        for _ in range(12):
            delta = target - float(np.sum(x))
            if abs(delta) < 1e-12:
                break
            if delta > 0:
                room = 1.0 - x
            else:
                room = x
            total = float(np.sum(room))
            if total <= 1e-14:
                break
            x = np.clip(x + delta * room / total, 0.0, 1.0)
        return x

    def correlations(x, with_jacobian=False):
        h = mapping @ x + fixed
        values = np.empty(len(pairs), dtype=float)
        jac = np.empty((len(pairs), p), dtype=float) if with_jacobian else None

        for q, (ii, jj) in enumerate(pairs):
            values[q] = np.sum(h[ii] * (1.0 - h[jj]))
            if with_jacobian:
                gh = np.zeros(n, dtype=float)
                np.add.at(gh, ii, 1.0 - h[jj])
                np.add.at(gh, jj, -h[ii])
                jac[q] = gh @ mapping
        return (values, jac) if with_jacobian else values

    def make_start(kind):
        if kind == 0:
            # Long alternating blocks, with slight boundary smoothing.
            block = np.repeat([0.03, 0.97], p // 8 + 1)[:p]
            x = np.tile(block, 4)[:p]
        elif kind == 1:
            # A low-discrepancy (van der Corput) binary pattern.
            vals = []
            for k in range(1, p + 1):
                v = 0
                t = k
                place = 0.5
                while t:
                    v += (t & 1) * place
                    t >>= 1
                    place *= 0.5
                vals.append(0.08 if v < 0.5 else 0.92)
            x = np.asarray(vals)
        elif kind == 2:
            # Smoothed random balanced pattern.
            raw = rng.random(p)
            x = 0.12 + 0.76 * (raw > np.median(raw))
            x = 0.65 * x + 0.35 * np.roll(x, 1)
        else:
            x = rng.uniform(0.02, 0.98, p)
        return project_balance(x)

    def objective(v):
        return float(v[-1])

    def objective_jac(v):
        out = np.zeros(p + 1)
        out[-1] = 1.0
        return out

    def equality(v):
        return np.sum(v[:p]) - p / 2.0

    def equality_jac(v):
        out = np.zeros(p + 1)
        out[:p] = 1.0
        return out

    def inequalities(v):
        c = correlations(v[:p])
        return v[-1] - c

    def inequalities_jac(v):
        _, dc = correlations(v[:p], with_jacobian=True)
        out = np.zeros((len(pairs), p + 1))
        out[:, :p] = -dc
        out[:, -1] = 1.0
        return out

    best_x = None
    best_value = float("inf")

    # Several substantially different starts reduce sensitivity to local
    # stationary points of this nonconvex quadratic epigraph formulation.
    for kind in range(7):
        start = make_start(kind)
        c = correlations(start)
        initial = np.concatenate([start, [float(np.max(c) + 1e-5)]])
        result = minimize(
            objective,
            initial,
            jac=objective_jac,
            method="SLSQP",
            bounds=[(0.0, 1.0)] * p + [(0.0, float(n))],
            constraints=[
                {"type": "eq", "fun": equality, "jac": equality_jac},
                {"type": "ineq", "fun": inequalities,
                 "jac": inequalities_jac},
            ],
            options={"maxiter": 450, "ftol": 2e-10, "disp": False},
        )
        candidate = project_balance(result.x[:p])
        value = float(np.max(correlations(candidate)))
        if value < best_value:
            best_value = value
            best_x = candidate

    if best_x is None:
        best_x = project_balance(make_start(0))

    # Correct the affine constraint one last time while respecting bounds.
    best_x = project_balance(best_x)
    answer = np.concatenate([best_x, [0.5]])
    answer[-1] = 0.5
    return answer.tolist()
# EVOLVE_END


if __name__ == "__main__":
    print(json.dumps({"half_sequence": generate_erdos_data()}))
```
