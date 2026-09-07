Mechanisms and strategies enabling successful optimization of high-dimensional non-negative step function sequences for autocorrelation inequality upper bounds.

- LogSumExp-Smoothed L-BFGS-B Optimization: Formulates the 600-dimensional non-negative sequence search as a continuous numerical optimization problem using L-BFGS-B to optimize all coefficients directly under non-negativity bounds.
- LogSumExp smooth maximum approximation: Replaces the non-differentiable max operation on the convolved sequence with a LogSumExp (LSE) function controlled by a stiffness parameter alpha, enabling stable gradient-based optimization.
- Continuation method: Progressively increases the stiffness parameter alpha over sequential L-BFGS-B runs while initializing each step with the solution of the previous step, achieving an upper bound score of 1.5145118627206406 with validity 1.0 within 41.89 seconds.

```python
#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
import time
from scipy.optimize import minimize

def search_for_best_sequence():
    """Generate optimized step function using continuation method and L-BFGS-B."""
    dimension = 600
    interval_start = -1 / 4
    interval_end = 1 / 4

    # Initial candidate
    x = np.linspace(interval_start, interval_end, dimension)
    step_function = 1.0 + 4.0 * np.abs(x) - 16.0 * x**2
    step_function = (step_function + step_function[::-1]) / 2
    step_function = np.maximum(step_function, 0)
    step_function /= np.sum(step_function)

    def objective(x, alpha):
        N = len(x)
        S = np.sum(x)
        if S < 1e-8:
            return 1e9, -np.ones(N)
        
        a = x / S
        b = np.convolve(a, a)
        max_b = np.max(b)
        
        b_shift = b - max_b
        # Ignore underflow warnings when alpha * b_shift is very negative
        with np.errstate(under='ignore'):
            exp_b = np.exp(alpha * b_shift)
        sum_exp = np.sum(exp_b)
        
        # LogSumExp (LSE) approximation of max(b)
        L = max_b + np.log(sum_exp) / alpha
        p = exp_b / sum_exp
        
        # Scale-invariant objective with LSE, plus regularization to fix scale S ≈ 1
        J = 2 * N * L + (S - 1)**2
        
        # Gradient of LSE with respect to `a`
        grad_L_wrt_a = 2 * np.correlate(p, a, mode='valid')
        grad_a_J = 2 * N * grad_L_wrt_a
        
        # Backpropagate gradient to `x` (accounting for normalization a = x / S)
        dot_product = np.sum(grad_a_J * a)
        grad_x_J = (grad_a_J - dot_product) / S + 2 * (S - 1)
        
        return J, grad_x_J

    x0 = step_function
    bounds = [(0, None)] * dimension
    
    # Continuation method: progressively increase stiffness of the smooth maximum
    alphas = np.logspace(4, 7, 10)
    start_time = time.time()
    
    for alpha in alphas:
        # Enforce the 1000 seconds time limit
        if time.time() - start_time > 900:
            break
            
        res = minimize(
            objective,
            x0,
            args=(alpha,),
            method='L-BFGS-B',
            jac=True,
            bounds=bounds,
            options={'maxiter': 5000, 'ftol': 1e-11, 'gtol': 1e-9}
        )
        x0 = res.x
        
    return x0.tolist()
# EVOLVE_END


def evaluate_sequence(sequence: list[float]) -> float:
    if not isinstance(sequence, list) or not sequence:
        return np.inf
    for value in sequence:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return np.inf
        if np.isnan(value) or np.isinf(value):
            return np.inf
    sequence = [min(1000.0, max(0.0, float(value))) for value in sequence]
    total = np.sum(sequence)
    if total < 0.01:
        return np.inf
    convolution = np.convolve(sequence, sequence)
    return float(2 * len(sequence) * max(convolution) / total**2)


def run_search_for_best_sequence():
    return list(search_for_best_sequence())


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))

```
