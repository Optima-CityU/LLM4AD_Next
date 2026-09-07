Post-processing smooth surrogate optimization with hard-max projected subgradient descent equalizes near-tied convolution peaks and optimizes non-differentiable objectives directly.

- L-BFGS-B optimization with LogSumExp surrogate: L-BFGS-B optimizes smoothed LogSumExp surrogates efficiently but leaves small residual gaps between near-tied peaks due to the smooth maximum approximation.
- Hard-max projected subgradient polishing: Appending a projected subgradient descent phase directly optimizes the non-differentiable maximum of the self-convolution by averaging the subgradients of all active top peaks within a 1e-6 tolerance.
- Simplex projection and backtracking line search: Projecting updates onto the probability simplex and accepting steps only when the exact non-differentiable objective strictly decreases enables precise equalization of dominant peaks, achieving an upper bound of 1.5145118627206402 and a target ratio of 0.9939176027950735.

```python
#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
import time
import numpy as np
from scipy.optimize import minimize

def search_for_best_sequence():
    """Generate optimized step function using continuation method and L-BFGS-B, followed by hard-max subgradient polishing."""
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
        # Enforce the time limit, leaving ample time for the polishing phase
        if time.time() - start_time > 800:
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

    # Polishing phase: hard-max projected subgradient descent with backtracking line search
    x = x0 / np.sum(x0)
    
    def project_simplex(v):
        """Project vector v onto the probability simplex."""
        n = len(v)
        u = np.sort(v)[::-1]
        cssv = np.cumsum(u)
        rho = np.nonzero(u * np.arange(1, n + 1) > (cssv - 1))[0][-1]
        theta = (cssv[rho] - 1) / (rho + 1.0)
        return np.maximum(v - theta, 0)
    
    lr = 1e-3
    best_x = x.copy()
    b_init = np.convolve(x, x)
    best_obj = 2 * dimension * np.max(b_init) / (np.sum(x)**2)
    
    while time.time() - start_time < 980:
        b = np.convolve(x, x)
        max_b = np.max(b)
        
        # Average subgradient of the active top peaks
        active_indices = np.where(b >= max_b - 1e-6)[0]
        p = np.zeros_like(b)
        p[active_indices] = 1.0 / len(active_indices)
        
        grad = 2 * np.correlate(p, x, mode='valid')
        
        accepted = False
        current_lr = lr
        
        # Backtracking line search
        for _ in range(20):
            x_new = x - current_lr * grad
            x_new = project_simplex(x_new)
            
            sum_new = np.sum(x_new)
            if sum_new < 1e-8:
                obj_new = np.inf
            else:
                b_new = np.convolve(x_new, x_new)
                obj_new = 2 * dimension * np.max(b_new) / (sum_new**2)
            
            # Strictly accept only if exact objective decreases
            if obj_new < best_obj:
                best_obj = obj_new
                best_x = x_new.copy()
                x = x_new
                accepted = True
                lr = current_lr * 1.2  # Slightly increase base learning rate on success
                break
            else:
                current_lr *= 0.5
                
        if not accepted:
            lr *= 0.5
            if lr < 1e-12:
                break
                
    return best_x.tolist()
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
