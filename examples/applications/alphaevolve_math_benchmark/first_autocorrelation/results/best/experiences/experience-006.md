Insight on why the continuation + scale-invariant LSE approach with asymmetric multi-start and upsampling performed well.

- Asymmetric Multi-Start Optimizer with Scale-Invariant Continuation: Coupling a LogSumExp-smoothed max-convolution objective with a continuation schedule whose temperature is scaled to the data (via the current auto-convolution max) and adding a scale-invariant penalty that anchors sum(a)≈1 stabilized gradients and prevented scaling drift, allowing L-BFGS-B to reliably refine asymmetric candidates selected by multi-start and then upsampled from n=600 to n=1200; in this run, that combination produced a valid result with upper_bound 1.510570583719461 and target_ratio 0.9965108656448988 in about 293.5 seconds. Reuse this pattern for objectives with non-differentiable maxima by: (1) starting from diverse asymmetric initializations, (2) optimizing under an LSE surrogate whose temperature is tied to the signal scale, (3) gradually hardening the surrogate via continuation up to very high temperatures, and (4) performing a final polish at higher resolution after upsampling.

```python
#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
import time
from scipy.optimize import minimize

def exact_evaluate(a):
    """Computes the exact evaluation function for a given sequence."""
    n = len(a)
    b = np.convolve(a, a)
    max_b = np.max(b)
    sum_a = np.sum(a)
    if sum_a < 1e-5:
        return float('inf')
    return 2 * n * max_b / (sum_a ** 2)

def smooth_obj_and_grad(a, beta_scale=100.0, gamma=10.0):
    """
    Computes a smooth approximation of the objective and its gradient.
    Incorporates a LogSumExp approximation for the max function and a 
    scale-invariant formulation with a penalty term to keep sum(a) ~ 1.
    """
    n = len(a)
    b = np.convolve(a, a)
    sum_a = np.sum(a)
    
    if sum_a < 1e-5:
        return 1e9, -np.ones(n)
        
    max_b = np.max(b)
    # Scale beta relative to max_b to maintain consistent temperature behavior
    beta = beta_scale / max(max_b, 1e-5)
    
    # Shift to avoid numerical overflow in exp, clipping extremely small values
    scaled_b = np.clip((b - max_b) * beta, -700, 0)
    exp_b = np.exp(scaled_b)
    sum_exp = np.sum(exp_b)
    
    # LogSumExp approximation of max(b)
    lse_b = max_b + np.log(sum_exp) / beta
    
    # Objective with penalty term to prevent scaling drift
    obj = 2 * n * lse_b / (sum_a ** 2) + gamma * (sum_a - 1.0)**2
    
    # Gradient computation
    p = exp_b / sum_exp
    grad_lse_b = np.correlate(p, 2 * a, mode='valid')
    grad = 2 * n * (grad_lse_b / (sum_a ** 2) - 2 * lse_b / (sum_a ** 3)) + 2 * gamma * (sum_a - 1.0)
    
    return obj, grad

def search_for_best_sequence():
    """Generate optimized step function using L-BFGS-B, multi-start, upsampling, and continuation."""
    start_time = time.time()
    n = 600
    best_obj = float('inf')
    best_seq = None
    initializations = []
    
    # 1. Generate diverse initializations (Exponentials, Power laws, Arcsine)
    x = np.linspace(0.001, 0.999, n)
    for c in [1.0, 1.256, 1.5, 2.0, 2.5, 3.0]:
        initializations.append(np.exp(c * x))
        initializations.append(np.exp(-c * x))
    for p in [0.5, -0.5, 0.25, -0.25, 0.75, -0.75, 1.5, 2.0]:
        initializations.append(x**p)
        initializations.append((1-x)**p)
    initializations.append(1.0 / np.sqrt(x * (1 - x)))
    
    # 2. Add random noise initializations to explore highly unstructured spaces
    np.random.seed(42)
    for _ in range(10):
        initializations.append(np.random.rand(n))
        initializations.append(np.random.rand(n) * np.exp(x))
        initializations.append(np.random.rand(n) * np.exp(-x))
        
    # 3. Add noise to ALL initializations to strictly break any symmetric mathematical bounds
    noisy_initializations = []
    for a0 in initializations:
        noise = np.random.rand(n) * 1e-3 * np.max(a0)
        noisy_initializations.append(a0 + noise)
    
    # Phase 1: Try diverse asymmetric initializations at base resolution
    beta_scale_init = 100.0
    gamma = 10.0
    
    for a0 in noisy_initializations:
        if time.time() - start_time > 400:
            break
            
        a0 = np.maximum(a0, 1e-6)
        if np.sum(a0) < 1e-6:
            continue
        a0 = a0 / np.sum(a0) # Normalize sum to 1 to match penalty formulation
        
        res = minimize(
            fun=lambda a: smooth_obj_and_grad(a, beta_scale=beta_scale_init, gamma=gamma)[0],
            x0=a0,
            jac=lambda a: smooth_obj_and_grad(a, beta_scale=beta_scale_init, gamma=gamma)[1],
            bounds=[(0, None)] * n,
            method='L-BFGS-B',
            options={'maxiter': 1000}
        )
        val = exact_evaluate(res.x)
        if val < best_obj:
            best_obj = val
            best_seq = res.x
            
    # Phase 2 & 3: Upsample and Continuation Method
    if best_seq is not None:
        # Upsample to a higher resolution (n=1200) to capture finer continuous details
        n2 = 1200
        x_old = np.linspace(0, 1, n)
        x_new = np.linspace(0, 1, n2)
        best_seq_n2 = np.interp(x_new, x_old, best_seq)
        best_seq_n2 = np.maximum(best_seq_n2, 1e-6)
        best_seq_n2 = best_seq_n2 / np.sum(best_seq_n2)
        
        # Exponentially increase the LogSumExp temperature to tightly polish the sequence
        beta_scales = [100.0, 500.0, 2000.0, 10000.0, 50000.0, 200000.0, 1e6, 1e7, 1e8]
        current_seq = best_seq_n2
        
        for beta in beta_scales:
            time_left = 950 - (time.time() - start_time)
            if time_left <= 0:
                break
                
            res = minimize(
                fun=lambda a: smooth_obj_and_grad(a, beta_scale=beta, gamma=gamma)[0],
                x0=current_seq,
                jac=lambda a: smooth_obj_and_grad(a, beta_scale=beta, gamma=gamma)[1],
                bounds=[(0, None)] * n2,
                method='L-BFGS-B',
                options={'maxiter': 1000, 'ftol': 1e-7}
            )
            current_seq = res.x
            val = exact_evaluate(current_seq)
            if val < best_obj:
                best_obj = val
                best_seq = current_seq
                
    # Fallback and final normalizations to ensure output safety
    if best_seq is None:
        best_seq = np.ones(n)
    best_seq = np.array(best_seq)
    max_val = np.max(best_seq)
    if max_val > 0:
        best_seq = best_seq / max_val * 100.0
        
    return best_seq.tolist()
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
