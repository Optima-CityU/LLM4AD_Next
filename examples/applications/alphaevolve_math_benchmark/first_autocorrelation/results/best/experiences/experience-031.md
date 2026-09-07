Key design mechanisms contributing to the 0.99577 target ratio (upper bound 1.51169) through extended LogSumExp continuation and adaptive dual-peak shaving fallback.

- Extended L-BFGS-B continuation schedule: The algorithm expands the LogSumExp surrogate continuation schedule to 14 steps reaching alpha = 10^8.0, achieving tighter smooth convergence prior to non-smooth polishing.
- Expanded cutting-plane refinement: The cutting-plane refinement targets the top 40 convolution peaks (increased from 32) weighted with relative temperature softmax, stabilizing descent across wide active plateaus.
- Adaptive dual-peak shaving mechanism: The multi-pair shaving step evaluates the top 2 worst convolution peaks, shaving up to 32 contributing pairs and redistributing mass to the 30 lowest-pressure indices, while falling back to the runner-up peak when shaving the primary peak causes see-saw stagnation.

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
    """Generate optimized step function using FFT-accelerated L-BFGS-B on softmax logits, followed by advanced non-smooth polishing."""
    dimension = 600
    interval_start = -1 / 4
    interval_end = 1 / 4

    # Initial candidate
    x = np.linspace(interval_start, interval_end, dimension)
    step_function = 1.0 + 4.0 * np.abs(x) - 16.0 * x**2
    step_function = (step_function + step_function[::-1]) / 2
    step_function = np.maximum(step_function, 1e-12)
    step_function /= np.sum(step_function)

    def objective_logits(logits, alpha):
        N = len(logits)
        
        # Softmax to enforce simplex constraint exactly
        max_l = np.max(logits)
        exp_l = np.exp(logits - max_l)
        sum_exp_l = np.sum(exp_l)
        a = exp_l / sum_exp_l
        
        # FFT-accelerated convolution
        A = np.fft.rfft(a, 2 * N - 1)
        b = np.fft.irfft(A * A, 2 * N - 1)
        
        max_b = np.max(b)
        b_shift = b - max_b
        
        # LogSumExp (LSE) approximation of max(b)
        with np.errstate(under='ignore'):
            exp_b = np.exp(alpha * b_shift)
        sum_exp_b = np.sum(exp_b)
        
        L = max_b + np.log(sum_exp_b) / alpha
        p = exp_b / sum_exp_b
        
        # Objective (Scale-invariant, but softmax already fixes sum(a)=1)
        J = 2 * N * L
        
        # Gradient of LSE with respect to `a` using FFT cross-correlation
        P = np.fft.rfft(p, 2 * N - 1)
        grad_L_wrt_a = 2 * np.fft.irfft(P * np.conj(A), 2 * N - 1)[:N]
        grad_a_J = 2 * N * grad_L_wrt_a
        
        # Backpropagate gradient through softmax to logits
        dot_product = np.sum(grad_a_J * a)
        grad_logits = a * (grad_a_J - dot_product)
        
        return J, grad_logits

    logits0 = np.log(step_function)
    
    # Continuation method: progressively increase stiffness of the smooth maximum
    alphas = np.logspace(4, 8.0, 14)
    start_time = time.time()
    
    for alpha in alphas:
        # Enforce the time limit, leaving ample time for the polishing phase
        if time.time() - start_time > 600:
            break
            
        res = minimize(
            objective_logits,
            logits0,
            args=(alpha,),
            method='L-BFGS-B',
            jac=True,
            options={'maxiter': 5000, 'ftol': 1e-11, 'gtol': 1e-9}
        )
        logits0 = res.x

    # Recover the probability distribution
    max_l = np.max(logits0)
    exp_l = np.exp(logits0 - max_l)
    x = exp_l / np.sum(exp_l)
    
    def project_simplex(v):
        """Project vector v onto the probability simplex."""
        n = len(v)
        u = np.sort(v)[::-1]
        cssv = np.cumsum(u)
        rho = np.nonzero(u * np.arange(1, n + 1) > (cssv - 1))[0][-1]
        theta = (cssv[rho] - 1) / (rho + 1.0)
        return np.maximum(v - theta, 0)
    
    def evaluate(seq):
        s = np.sum(seq)
        if s < 1e-8:
            return np.inf, None
        b_seq = np.convolve(seq, seq)
        obj = 2 * dimension * np.max(b_seq) / (s**2)
        return obj, b_seq

    lr = 1e-3
    best_x = x.copy()
    best_obj, b = evaluate(x)
    
    iteration = 0
    
    # Polishing phase: hard-max projected subgradient descent with advanced refinements
    while time.time() - start_time < 980:
        iteration += 1
        max_b = np.max(b)
        temp = max(max_b * 1e-5, 1e-12)
        
        # 1. Standard Subgradient Descent (Average of active top peaks)
        active_indices = np.where(b >= max_b - temp)[0]
        p = np.zeros_like(b)
        p[active_indices] = 1.0 / len(active_indices)
        
        grad = 2 * np.correlate(p, x, mode='valid')
        
        accepted = False
        current_lr = lr
        
        # Backtracking line search
        for _ in range(20):
            x_new = x - current_lr * grad
            x_new = project_simplex(x_new)
            
            obj_new, b_new = evaluate(x_new)
            
            if obj_new < best_obj:
                best_obj = obj_new
                best_x = x_new.copy()
                x = x_new
                b = b_new
                accepted = True
                lr = current_lr * 1.2  # Slightly increase base learning rate on success
                break
            else:
                current_lr *= 0.5
                
        if not accepted:
            lr *= 0.5
            
        # 2. Multi-peak Cutting-Plane Refinement (Top M peaks)
        if iteration % 3 == 0:
            M = min(40, len(b))
            top_M_indices = np.argsort(b)[-M:]
            
            p_m = np.zeros_like(b)
            weights = np.exp((b[top_M_indices] - max_b) / temp)
            p_m[top_M_indices] = weights / np.sum(weights)
            
            grad_m = 2 * np.correlate(p_m, x, mode='valid')
            
            current_lr_m = lr * 2.0
            for _ in range(15):
                x_new = x - current_lr_m * grad_m
                x_new = project_simplex(x_new)
                obj_new, b_new = evaluate(x_new)
                
                if obj_new < best_obj:
                    best_obj = obj_new
                    best_x = x_new.copy()
                    x = x_new
                    b = b_new
                    break
                else:
                    current_lr_m *= 0.5

        # 3. Symmetry Mixing
        if iteration % 7 == 0:
            x_sym = (x + x[::-1]) / 2.0
            x_sym = project_simplex(x_sym)
            obj_sym, b_sym = evaluate(x_sym)
            if obj_sym < best_obj:
                best_obj = obj_sym
                best_x = x_sym.copy()
                x = x_sym
                b = b_sym

        # 4. Multi-pair Shaving with Fractional Backtracking
        if iteration % 5 == 0:
            worst_peak_indices = np.argsort(b)[-2:][::-1]
            shave_successful = False
            
            for worst_peak_idx in worst_peak_indices:
                if shave_successful:
                    break
                    
                start_i = max(0, worst_peak_idx - dimension + 1)
                end_i = min(dimension - 1, worst_peak_idx)
                
                if end_i >= start_i:
                    i_vals = np.arange(start_i, end_i + 1)
                    contributions = x[i_vals] * x[worst_peak_idx - i_vals]
                    
                    top_k = min(32, len(contributions))
                    if top_k > 0:
                        if top_k == len(contributions):
                            top_idx = np.arange(len(contributions))
                        else:
                            top_idx = np.argpartition(contributions, -top_k)[-top_k:]
                        top_i = i_vals[top_idx]
                        
                        num_low = min(30, len(grad))
                        low_pressure_indices = np.argpartition(grad, num_low - 1)[:num_low]
                        
                        for scale in [1.0, 0.5, 0.25, 0.125]:
                            x_shave = x.copy()
                            mass_to_move = 0.0
                            processed = set()
                            for i in top_i:
                                j = worst_peak_idx - i
                                if i in processed or j in processed:
                                    continue
                                processed.add(i)
                                processed.add(j)
                                
                                shave_amount = scale * 1e-4 * min(x_shave[i], x_shave[j])
                                x_shave[i] -= shave_amount
                                mass_to_move += shave_amount
                                if i != j:
                                    x_shave[j] -= shave_amount
                                    mass_to_move += shave_amount
                                    
                            x_shave[low_pressure_indices] += mass_to_move / num_low
                            x_shave = project_simplex(x_shave)
                            
                            obj_new, b_new = evaluate(x_shave)
                            if obj_new < best_obj:
                                best_obj = obj_new
                                best_x = x_shave.copy()
                                x = x_shave
                                b = b_new
                                shave_successful = True
                                break
                            
        # Early stopping logic if learning rate vanishes
        if lr < 1e-12:
            # Try to reboot learning rate
            lr = 1e-4
                
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
