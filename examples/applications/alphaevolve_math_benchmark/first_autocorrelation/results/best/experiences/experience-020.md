Algorithmic design combining FFT-accelerated smooth optimization on unconstrained logits with non-smooth subgradient polishing and heuristic mass transport.

- Softmax L-BFGS-B continuation with FFT acceleration: Parameterizing step function heights via unconstrained logits through a softmax mapping inherently satisfies simplex and non-negativity constraints, while zero-padded FFT convolution and cross-correlation accelerate LogSumExp surrogate optimization during L-BFGS-B continuation.
- Hard-max projected subgradient polishing: Following smooth surrogate optimization, running hard-max projected subgradient descent with backtracking line search directly minimizes the exact non-differentiable maximum by averaging subgradients of active peak convolutions within tolerance.
- Multi-peak pair-shaving mass transport: Interleaving heuristic pair-shaving when subgradient descent stalls shifts mass from index pairs contributing most heavily to worst-case peaks to lower-pressure coordinates, escaping non-differentiable plateaus subject to strict objective decrease.
- FFT-Accelerated Softmax L-BFGS-B with Hard-Max Polish and Pair-Shaving: The hybrid algorithm achieved an optimization score of 0.9940258090868428 with an upper bound metric of 1.5143469980752682 across a 600-interval step function discretization.

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
    """Generate optimized step function using continuation method and L-BFGS-B on unconstrained logits (with FFT speedups), followed by hard-max subgradient polishing and pair-shaving mass transport."""
    dimension = 600
    interval_start = -1 / 4
    interval_end = 1 / 4

    # Initial candidate
    x = np.linspace(interval_start, interval_end, dimension)
    step_function = 1.0 + 4.0 * np.abs(x) - 16.0 * x**2
    step_function = (step_function + step_function[::-1]) / 2
    step_function = np.maximum(step_function, 0)
    step_function /= np.sum(step_function)

    def fft_conv(a, b_seq):
        """Zero-padded FFT convolution for speed."""
        N = len(a)
        n_fft = 2048
        A = np.fft.rfft(a, n=n_fft)
        B = np.fft.rfft(b_seq, n=n_fft)
        return np.fft.irfft(A * B, n=n_fft)[:2*N-1]

    def fft_corr(p, a):
        """Zero-padded FFT cross-correlation for speed."""
        N = len(a)
        n_fft = 2048
        P = np.fft.rfft(p, n=n_fft)
        A_rev = np.fft.rfft(a[::-1], n=n_fft)
        return np.fft.irfft(P * A_rev, n=n_fft)[N-1:2*N-1]

    def objective(z, alpha):
        N = len(z)
        
        # Unconstrained softmax parameterization (perfectly enforces simplex constraints)
        z_max = np.max(z)
        exp_z = np.exp(z - z_max)
        a = exp_z / np.sum(exp_z)
        
        # Fast convolution
        b = fft_conv(a, a)
        max_b = np.max(b)
        
        # LogSumExp (LSE) approximation of max(b)
        b_shift = b - max_b
        with np.errstate(under='ignore'):
            exp_b = np.exp(alpha * b_shift)
        sum_exp = np.sum(exp_b)
        
        L = max_b + np.log(sum_exp) / alpha
        p = exp_b / sum_exp
        
        # Scale-invariant objective
        J = 2 * N * L
        
        # Gradient of LSE with respect to `a`
        grad_L_wrt_a = 2 * fft_corr(p, a)
        grad_a_J = 2 * N * grad_L_wrt_a
        
        # Backpropagate gradient to logits `z` (Jacobian of softmax)
        dot_product = np.sum(grad_a_J * a)
        grad_z_J = a * (grad_a_J - dot_product)
        
        return J, grad_z_J

    # Initialize logits
    z0 = np.log(step_function + 1e-15)
    
    # Continuation method: progressively increase stiffness of the smooth maximum
    alphas = np.logspace(4, 7.5, 12)
    start_time = time.time()
    
    for alpha in alphas:
        # Enforce the time limit, leaving ample time for the polishing phase
        if time.time() - start_time > 750:
            break
            
        res = minimize(
            objective,
            z0,
            args=(alpha,),
            method='L-BFGS-B',
            jac=True,
            options={'maxiter': 5000, 'ftol': 1e-12, 'gtol': 1e-10}
        )
        z0 = res.x

    # Polishing phase: hard-max projected subgradient descent with backtracking line search
    z_max = np.max(z0)
    exp_z = np.exp(z0 - z_max)
    x = exp_z / np.sum(exp_z)
    
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
    
    # Exact objective uses np.convolve to avoid any FFT artifacts in the final evaluation
    exact_b_init = np.convolve(x, x)
    best_obj = 2 * dimension * np.max(exact_b_init) / (np.sum(x)**2)
    
    stalls = 0
    
    while time.time() - start_time < 980:
        b = fft_conv(x, x)
        max_b = np.max(b)
        
        # Average subgradient of the active top peaks
        active_indices = np.where(b >= max_b - 1e-6)[0]
        p = np.zeros_like(b)
        p[active_indices] = 1.0 / len(active_indices)
        
        grad = 2 * fft_corr(p, x)
        
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
            if obj_new < best_obj - 1e-13:
                best_obj = obj_new
                best_x = x_new.copy()
                x = x_new
                accepted = True
                lr = current_lr * 1.2  # Slightly increase base learning rate on success
                stalls = 0
                break
            else:
                current_lr *= 0.5
                
        if not accepted:
            lr *= 0.5
            stalls += 1
            
        # Interleave multi-peak pair-shaving (mass transport) steps when stalled
        if stalls >= 3 or lr < 1e-9:
            b_exact = np.convolve(x, x)
            k = np.argmax(b_exact)
            
            # Find top contributing index pair for the worst peak
            i_vals = np.arange(max(0, k - dimension + 1), min(dimension, k + 1))
            contributions = x[i_vals] * x[k - i_vals]
            best_idx = np.argmax(contributions)
            i_max = i_vals[best_idx]
            j_max = k - i_max
            
            # Low-pressure index (min gradient indicates it reduces/doesn't increase peaks)
            m = np.argmin(grad)
            
            shaved = False
            for step_size in [1e-4, 5e-5, 1e-5, 5e-6, 1e-6, 1e-7]:
                x_new = x.copy()
                transfer = min(x_new[i_max], step_size)
                x_new[i_max] -= transfer
                x_new[m] += transfer
                x_new = project_simplex(x_new)
                
                b_new = np.convolve(x_new, x_new)
                obj_new = 2 * dimension * np.max(b_new) / (np.sum(x_new)**2)
                
                if obj_new < best_obj - 1e-13:
                    best_obj = obj_new
                    best_x = x_new.copy()
                    x = x_new
                    shaved = True
                    stalls = 0
                    lr = 1e-4
                    break
            
            if not shaved and i_max != j_max:
                for step_size in [1e-4, 5e-5, 1e-5, 5e-6, 1e-6, 1e-7]:
                    x_new = x.copy()
                    transfer1 = min(x_new[i_max], step_size)
                    transfer2 = min(x_new[j_max], step_size)
                    x_new[i_max] -= transfer1
                    x_new[j_max] -= transfer2
                    x_new[m] += transfer1 + transfer2
                    x_new = project_simplex(x_new)
                    
                    b_new = np.convolve(x_new, x_new)
                    obj_new = 2 * dimension * np.max(b_new) / (np.sum(x_new)**2)
                    
                    if obj_new < best_obj - 1e-13:
                        best_obj = obj_new
                        best_x = x_new.copy()
                        x = x_new
                        shaved = True
                        stalls = 0
                        lr = 1e-4
                        break
                        
            if not shaved and lr < 1e-12:
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
