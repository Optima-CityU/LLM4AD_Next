Actionable design insight from the observed algorithm run and its measured outcomes.

- Multi-scale Hierarchical Refinement Optimization: This method tackles the non-convex 50-variable search via a coarse-to-fine schedule (N=5→10→25→50), upsampling the best solution and injecting asymmetric noise at each transition to preserve global shape while exploring asymmetric extremals; at each level it alternates momentum-based gradient ascent on the scale-invariant objective L2^2/(L1*Linf) with simulated-annealing-style stochastic perturbations and re-optimization to escape local maxima; a vectorized fast_objective based on convolution with central finite-difference gradients (without clipping negative perturbations) enables efficient iterations, evidenced by validity 1.0, target_ratio 0.9717397326409261, c_lower_bound 0.870970322366062, and eval_time 37.57260389090516.

```python
#!/usr/bin/env python3
"""Initial step function for the second autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
def optimize_lower_bound():
    def fast_objective(h):
        """Compute the scale-invariant objective efficiently."""
        C = np.convolve(h, h)
        N = len(C) + 1
        # Equivalent to the original L2^2 computation but vectorized and O(N)
        l2_squared_sum = 2.0 * np.sum(C**2) + np.sum(C[:-1] * C[1:])
        l2_squared = l2_squared_sum / (3.0 * N)
        l1 = (np.sum(h))**2 / N
        linf = np.max(C)
        if l1 == 0 or linf == 0:
            return 0.0
        return float(l2_squared / (l1 * linf))

    def objective_and_grad(h):
        """Compute objective and gradient using central finite differences."""
        obj = fast_objective(h)
        eps = 1e-5
        grad = np.zeros_like(h)
        h_p = h.copy()
        h_m = h.copy()
        for i in range(len(h)):
            h_p[i] += eps
            obj_p = fast_objective(h_p)
            h_p[i] = h[i]  # reset
            
            h_m[i] -= eps
            # We don't clip h_m to 0 here to get true local gradient,
            # fast_objective can handle slightly negative values mathematically.
            obj_m = fast_objective(h_m)
            h_m[i] = h[i]  # reset
            
            grad[i] = (obj_p - obj_m) / (2 * eps)
        return obj, grad

    def adam_optimize(h_init, iters=1000, lr=0.01):
        """Maximize the objective using Adam optimizer."""
        h = h_init.copy()
        m = np.zeros_like(h)
        v = np.zeros_like(h)
        beta1 = 0.9
        beta2 = 0.999
        eps = 1e-8
        
        best_h = h.copy()
        best_obj = fast_objective(h)
        
        for t in range(1, iters + 1):
            obj, grad = objective_and_grad(h)
            
            if obj > best_obj:
                best_obj = obj
                best_h = h.copy()
                
            m = beta1 * m + (1 - beta1) * grad
            v = beta2 * v + (1 - beta2) * (grad ** 2)
            m_hat = m / (1 - beta1 ** t)
            v_hat = v / (1 - beta2 ** t)
            
            # Gradient ascent (maximizing)
            h = h + lr * m_hat / (np.sqrt(v_hat) + eps)
            h = np.maximum(h, 0.0)
            max_h = np.max(h)
            if max_h > 0:
                h = h / max_h
                
        # Final check
        obj = fast_objective(h)
        if obj > best_obj:
            best_obj = obj
            best_h = h.copy()
            
        return best_h, best_obj

    def perturb(h, temp):
        """Apply random perturbations for simulated annealing."""
        noise = np.random.normal(0, temp, size=h.shape)
        h_new = h + noise
        h_new = np.maximum(h_new, 0.0)
        max_h = np.max(h_new)
        if max_h > 0:
            h_new = h_new / max_h
        else:
            h_new = np.ones_like(h)
        return h_new

    grid_sizes = [5, 10, 25, 50]
    np.random.seed(42)  # For reproducibility
    
    # 1. Initialize coarse grid with multiple random restarts
    best_initial_h = None
    best_initial_obj = -1
    for _ in range(20):
        h_cand = np.random.uniform(0.1, 1.0, size=grid_sizes[0])
        h_cand = h_cand / np.max(h_cand)
        h_cand, obj_cand = adam_optimize(h_cand, iters=500, lr=0.02)
        if obj_cand > best_initial_obj:
            best_initial_obj = obj_cand
            best_initial_h = h_cand
            
    h = best_initial_h
    
    # 2. Loop through grid sizes for coarse-to-fine refinement
    for i, N in enumerate(grid_sizes):
        # 3. Optimize using gradient ascent
        iters = 2000 if N < 50 else 5000
        lr = 0.01 if N < 50 else 0.005
        h, obj = adam_optimize(h, iters=iters, lr=lr)
        
        # 4. Simulated annealing / stochastic perturbations
        best_h_level = h.copy()
        best_obj_level = obj
        
        num_perts = 5 if N < 50 else 3
        for step in range(num_perts):
            temp = 0.1 * (0.5 ** step)
            h_pert = perturb(best_h_level, temp)
            h_opt, obj_opt = adam_optimize(h_pert, iters=iters // 2, lr=lr)
            
            if obj_opt > best_obj_level:
                best_obj_level = obj_opt
                best_h_level = h_opt
                
        h = best_h_level
        
        # 5. Progressive upsampling
        if N < 50:
            next_N = grid_sizes[i+1]
            x_old = np.linspace(0, 1, N)
            x_new = np.linspace(0, 1, next_N)
            h_up = np.interp(x_new, x_old, h)
            
            # Symmetry breaking noise
            noise = np.random.normal(0, 0.05, size=next_N)
            h = np.maximum(h_up + noise, 0.0)
            h = h / np.max(h)
            
    # Final evaluation using the exact original logic
    convolution = np.convolve(h, h)
    widths = np.diff(np.linspace(-0.5, 0.5, len(convolution) + 2))
    values = np.concatenate(([0.0], convolution, [0.0]))
    l2_squared = sum(
        widths[index] / 3.0 * (values[index] ** 2 + values[index] * values[index + 1] + values[index + 1] ** 2)
        for index in range(len(convolution) + 1)
    )
    l1 = float(np.sum(np.abs(convolution)) / (len(convolution) + 1))
    linf = float(np.max(np.abs(convolution)))
    c_lower_bound = float(l2_squared / (l1 * linf))
    
    return [float(x) for x in h], c_lower_bound
# EVOLVE_END


if __name__ == "__main__":
    heights, c_lower_bound = optimize_lower_bound()
    print(json.dumps({"heights": heights, "c_lower_bound": c_lower_bound}))
```
