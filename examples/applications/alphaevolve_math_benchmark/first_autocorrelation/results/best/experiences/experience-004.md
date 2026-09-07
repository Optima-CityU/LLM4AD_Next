Smoothed maximum with analytic gradients and bound-constrained L-BFGS-B, warm-started and annealed, for convolution-based objectives.

- Smooth Minimax Convolution Optimization via L-BFGS-B: Replacing the non-differentiable max in the convolution-based objective with a smooth LogSumExp surrogate enabled analytic gradients (using the identity d(a*a)_k/da_i = 2·a_{k−i}) and bound-constrained L-BFGS-B steps under non-negativity, and progressively tightening the smoothing parameter (annealing) while warm-starting from the 600-point polynomial shape 1 + 4|x| − 16x^2 on [-1/4, 1/4] delivered stable convergence within the 1000-second budget and achieved upper_bound = 1.5122923472839314 with target_ratio = 0.9953763256843232 and validity = 1.0 (eval_time ≈ 951.315s); future designs facing max-over-convolution objectives with non-negativity constraints should reuse this smoothed-max + annealed L-BFGS-B pattern and the domain-shaped warm start to obtain reliable improvements under tight time limits.

```python
#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json
import numpy as np

# EVOLVE_START
import time
from scipy.optimize import minimize

class TimeLimitException(Exception):
    pass

def search_for_best_sequence():
    """
    Formulates the search for the optimal sequence as a continuous optimization problem.
    Uses a smooth maximum approximation (LogSumExp) to handle the non-differentiable max operation.
    Computes analytical gradients and uses L-BFGS-B to minimize the objective.
    The smoothing parameter is progressively tightened to approach the true maximum,
    allowing the optimizer to dynamically adjust all 600 coefficients.
    """
    dimension = 600
    interval_start = -1 / 4
    interval_end = 1 / 4

    # Initial warm-start using the known good polynomial shape
    x = np.linspace(interval_start, interval_end, dimension)
    step_function = 1.0 + 4.0 * np.abs(x) - 16.0 * x**2
    step_function = (step_function + step_function[::-1]) / 2
    step_function = np.maximum(step_function, 0)
    
    u = step_function / np.sum(step_function)
    
    start_time = time.time()
    time_limit = 950 # Leave a 50-second buffer to ensure safe return before 1000s
    
    # Progressively tighten the smoothing parameter
    alphas = np.logspace(3.5, 8.0, 25)
    lambda_reg = 1.0
    
    best_u = u.copy()
    
    # Safe evaluation function to track the true best sequence
    def evaluate_true(seq):
        seq = np.maximum(0, seq)
        total = np.sum(seq)
        if total < 0.01:
            return np.inf
        convolution = np.convolve(seq, seq)
        return float(2 * len(seq) * np.max(convolution) / total**2)
        
    best_val = evaluate_true(best_u)
    
    def make_callback():
        def callback(xk):
            if time.time() - start_time > time_limit:
                raise TimeLimitException()
        return callback

    pass_num = 0
    try:
        while time.time() - start_time < time_limit:
            pass_num += 1
            
            # Multi-start strategy to explore different regions
            if pass_num == 1:
                u = best_u.copy()
            elif pass_num == 2:
                # Try a flat sequence
                u = np.ones(dimension) / dimension
            elif pass_num == 3:
                # Try a triangle shape
                u = 1.0 - np.abs(np.linspace(-1, 1, dimension))
                u = np.maximum(u, 0)
                u = u / np.sum(u)
            else:
                # Perturb the best sequence found so far to escape local minima
                noise = np.random.normal(0, 0.005 * np.max(best_u), dimension)
                # Symmetrize the noise to encourage symmetric solutions
                noise = (noise + noise[::-1]) / 2
                u = best_u + noise
                u = np.maximum(u, 0)
                S = np.sum(u)
                if S > 1e-8:
                    u = u / S
                else:
                    u = best_u.copy()
                    
            for alpha in alphas:
                if time.time() - start_time > time_limit:
                    raise TimeLimitException()
                    
                def objective(u_opt):
                    S = np.sum(u_opt)
                    if S < 1e-8:
                        return 1e9, np.zeros_like(u_opt)
                    
                    a = u_opt / S
                    C = np.convolve(a, a)
                    C_max = np.max(C)
                    
                    # Compute smooth max using LogSumExp
                    # Shift by C_max to prevent numerical overflow
                    C_shifted = C - C_max
                    E = np.exp(alpha * C_shifted)
                    Z = np.sum(E)
                    w = E / Z
                    M_alpha = C_max + np.log(Z) / alpha
                    
                    # Objective and regularization (to keep sum close to 1)
                    J = 2 * dimension * M_alpha
                    reg = lambda_reg * (S - 1)**2
                    loss = J + reg
                    
                    # Analytical gradient computation
                    # The derivative of (a*a)_k wrt a_i is 2 * a_{k-i}
                    g = 2 * np.correlate(w, a, mode='valid')
                    v = 2 * np.sum(w * C)
                    
                    grad_u = 2 * dimension * (g - v) / S + 2 * lambda_reg * (S - 1)
                    return loss, grad_u
                    
                bounds = [(0, None) for _ in range(dimension)]
                
                res = minimize(
                    objective,
                    u,
                    method='L-BFGS-B',
                    jac=True,
                    bounds=bounds,
                    callback=make_callback(),
                    options={'maxiter': 5000, 'ftol': 1e-10, 'gtol': 1e-8}
                )
                
                u = res.x
                u = np.maximum(u, 0)
                S = np.sum(u)
                if S > 1e-8:
                    u = u / S
                
                # Evaluate true objective and update best
                val = evaluate_true(u)
                if val < best_val:
                    best_val = val
                    best_u = u.copy()
                    
    except TimeLimitException:
        pass # Time limit reached, safely exit the search and return best found
        
    return best_u.tolist()
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
