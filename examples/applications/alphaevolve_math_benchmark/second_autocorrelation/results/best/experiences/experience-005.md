Actionable design choices that lifted performance beyond 0.8962 by exploiting asymmetry and robust subgradient handling for non-smooth L_inf terms.

- Asymmetric Full-Space Hierarchical Adam with Generalized Subgradients: Removing the symmetry constraint and optimizing over the full 50-dimensional asymmetric step-function space unlocked higher-performing solutions; coupled with a generalized L_inf subgradient that targets the empirical argmax (averaging over ties) and a correct mapping from the 99-dimensional convolution derivative back to the 50 parameters, the batched Adam stages could reliably ascend a non-smooth objective. Interleaving exact 1D bounded line searches for pairwise mass swaps to escape local optima and finishing with L-BFGS-B polishing further improved the objective. Evidence: this configuration achieved c_lower_bound = 0.902963042793055 with validity = 1.0 (target_ratio = 1.0074339426453809), surpassing the 0.8962 target and improving over the symmetry-constrained parent (~0.8073832881513059). Reuse this pattern when optimizing convolutional max-norm ratios: remove unwarranted symmetry assumptions, implement argmax-tracking subgradients for L_inf terms, and combine continuous Adam updates with discrete mass-swaps plus a final smooth optimizer.

```python
#!/usr/bin/env python3
"""Initial step function for the second autocorrelation inequality."""

import json
import numpy as np
from scipy.optimize import minimize, minimize_scalar

# EVOLVE_START
def optimize_lower_bound():
    """
    Optimizes the step function to maximize the second autocorrelation inequality lower bound.
    Uses a highly optimized, multi-phase hierarchical Adam optimizer with exact analytical gradients,
    interleaved with exact 1D bounded line searches for pairwise mass swaps to escape 
    local optima, and followed by L-BFGS-B for final polishing.
    
    The search is performed over the full 50-dimensional asymmetric space to discover
    high-performing asymmetric step functions (like the Matolcsi and Vinuesa construction).
    """
    
    def compute_obj_and_grad_batch(x):
        """
        Computes the objective R and its gradient with respect to x for a batch of inputs.
        x is the full asymmetric sequence parameterization: h_i = x_i^2.
        """
        B = x.shape[0]
        h = x**2 # length 50
        
        # Batched convolution c = h * h using FFT (length 99)
        H = np.fft.rfft(h, n=256, axis=1)
        C = H * H
        c = np.fft.irfft(C, n=256, axis=1)[:, :99]
        
        # Pad c with 0s for L2_sq calculation
        z = np.zeros((B, 1))
        v = np.concatenate((z, c, z), axis=1) # length 101
        
        # Calculate L2 squared norm
        L2_sq = np.sum(v[:, :-1]**2 + v[:, :-1]*v[:, 1:] + v[:, 1:]**2, axis=1) / 300.0
        
        # Calculate L1 norm
        L1 = np.sum(c, axis=1) / 100.0
        
        # Calculate L_inf norm (maximum can be anywhere for asymmetric h)
        max_c = np.max(c, axis=1, keepdims=True)
        Linf = max_c[:, 0]
        
        # Objective R
        R = L2_sq / (L1 * Linf)
        
        # Gradients
        # 1. d L2_sq / d v
        dv = (np.roll(v, 1, axis=1) + 4*v + np.roll(v, -1, axis=1)) / 300.0
        dc_L2 = dv[:, 1:100] # valid for c_0 ... c_98
        
        # 2. d L1 / d c
        dc_L1 = np.ones((B, 99)) / 100.0
        
        # 3. d Linf / d c (averaging over ties)
        is_max = (c == max_c)
        dc_Linf = is_max / np.sum(is_max, axis=1, keepdims=True)
        
        # 4. d R / d c using quotient rule
        dc_R = (dc_L2 - (R * Linf)[:, None] * dc_L1 - (R * L1)[:, None] * dc_Linf) / (L1 * Linf)[:, None]
        
        # 5. d R / d h using cross-correlation
        DC_R = np.fft.rfft(dc_R, n=256, axis=1)
        H_conj = np.conj(np.fft.rfft(h, n=256, axis=1))
        dh_R_full = np.fft.irfft(DC_R * H_conj, n=256, axis=1)
        dh_R = 2 * dh_R_full[:, :50]
        
        # 6. d R / d x using chain rule
        dx_R = 2 * x * dh_R
        
        return R, dx_R

    def batched_adam(x_init, lr, num_iters):
        """Batched Adam optimizer for maximizing R."""
        x = x_init.copy()
        m = np.zeros_like(x)
        v = np.zeros_like(x)
        
        best_x = x.copy()
        best_R = np.full(x.shape[0], -1.0)
        
        beta1 = 0.9
        beta2 = 0.999
        eps = 1e-8
        
        for t in range(1, num_iters + 1):
            R, grad = compute_obj_and_grad_batch(x)
            
            # Track best candidates
            improved = R > best_R
            best_R[improved] = R[improved]
            best_x[improved] = x[improved]
            
            # Adam step (minimizing -R)
            g = -grad
            m = beta1 * m + (1 - beta1) * g
            v = beta2 * v + (1 - beta2) * (g ** 2)
            
            m_hat = m / (1 - beta1 ** t)
            v_hat = v / (1 - beta2 ** t)
            
            x = x - lr * m_hat / (np.sqrt(v_hat) + eps)
            
        # Normalize best_x to prevent scale drift in subsequent stages
        best_x = best_x / np.max(np.abs(best_x), axis=1, keepdims=True)
        return best_x, best_R

    def exact_objective_h(h):
        """Highly optimized exact objective function for 1D line searches."""
        c = np.convolve(h, h)
        # Simplified L2_sq calculation mathematically equivalent to the baseline
        L2_sq = (2.0 * np.sum(c**2) + np.sum(c[:-1] * c[1:])) / 300.0
        L1 = np.sum(c) / 100.0
        Linf = np.max(c)
        if L1 == 0 or Linf == 0:
            return 0.0
        return L2_sq / (L1 * Linf)

    def mass_swap_optimization(h_full):
        """
        Performs discrete local-optima-escaping mass swaps with exact 1D bounded line searches.
        Transfers mass between pairs of elements to breakthrough continuous local maxima.
        Operates over the full 50-dimensional space.
        """
        h = h_full.copy()
        improved = True
        best_R = exact_objective_h(h)
        
        sweep_count = 0
        while improved and sweep_count < 20:
            improved = False
            sweep_count += 1
            
            for i in range(50):
                for j in range(i + 1, 50):
                    if h[i] + h[j] < 1e-9:
                        continue
                        
                    def obj_delta(delta):
                        h_temp = h.copy()
                        h_temp[i] -= delta
                        h_temp[j] += delta
                        return -exact_objective_h(h_temp)
                    
                    # Evaluate grid to locate the global minimum basin on the line segment
                    deltas = np.linspace(-h[j], h[i], 11)
                    vals = np.array([obj_delta(d) for d in deltas])
                    min_idx = np.argmin(vals)
                    min_val = vals[min_idx]
                    best_d = deltas[min_idx]
                    
                    # Refine with exact bounded scalar optimization
                    res = minimize_scalar(
                        obj_delta, 
                        bounds=(-h[j], h[i]), 
                        method='bounded', 
                        options={'xatol': 1e-9, 'maxiter': 50}
                    )
                    
                    if res.success:
                        cand_val = -res.fun
                        cand_d = res.x
                    else:
                        cand_val = -np.inf
                        cand_d = 0.0
                    
                    # Fallback to grid best if scalar minimization got trapped
                    if -min_val > cand_val:
                        cand_val = -min_val
                        cand_d = best_d
                        
                    if cand_val > best_R + 1e-9:
                        best_R = cand_val
                        h[i] -= cand_d
                        h[j] += cand_d
                        # Enforce strict non-negativity against precision issues
                        h[i] = max(0.0, h[i])
                        h[j] = max(0.0, h[j])
                        improved = True
        return h, best_R

    # Generate diverse structured and random initializations across the full asymmetric space
    inits = []
    inits.append(np.ones(50)) # Constant
    for c in np.linspace(0, 1, 20): # Linear
        inits.append(np.linspace(1, 1-c, 50))
        inits.append(np.linspace(1-c, 1, 50))
    for c in np.linspace(0, 1, 20): # Quadratic
        inits.append(1 - c * np.linspace(0, 1, 50)**2)
        inits.append(1 - c * np.linspace(1, 0, 50)**2)
    for k in range(1, 50): # Step functions
        for v in np.linspace(0.0, 0.9, 10):
            x = np.ones(50)
            x[k:] = v
            inits.append(x)
    for freq in np.linspace(0.1, 5, 20): # Sine waves
        inits.append(np.abs(np.sin(freq * np.linspace(0, 1, 50))))
        inits.append(np.abs(np.cos(freq * np.linspace(0, 1, 50))))
        
    # 2-step functions (more granular structural search)
    for k1 in range(1, 48):
        for k2 in range(k1+1, 49):
            for v1 in [0.0, 0.3, 0.6]:
                for v2 in [0.0, 0.3, 0.6]:
                    x = np.ones(50)
                    x[k1:k2] = v1
                    x[k2:] = v2
                    inits.append(x)
                    
    np.random.seed(42)
    # Random binary and ternary sequences
    for _ in range(2000):
        inits.append(np.random.choice([1e-4, 1.0], size=50))
    for _ in range(2000):
        inits.append(np.random.choice([1e-4, 0.5, 1.0], size=50))
        
    # Multi-step functions (randomized boundaries and values) to directly target Matolcsi-Vinuesa structures
    for _ in range(3000):
        num_steps = np.random.randint(3, 8)
        boundaries = sorted(np.random.choice(range(1, 49), num_steps - 1, replace=False))
        boundaries = [0] + boundaries + [50]
        
        if np.random.rand() < 0.5:
            values = np.random.rand(num_steps)
        else:
            values = np.random.choice([1e-4, 0.5, 1.0], num_steps)
            
        x = np.zeros(50)
        for i in range(num_steps):
            x[boundaries[i]:boundaries[i+1]] = values[i]
        inits.append(x)
        
    num_random = max(0, 25000 - len(inits))
    random_inits = np.random.rand(num_random, 50)
    inits_array = np.vstack([np.array(inits), random_inits])
    # Ensure strictly positive initialization
    inits_array = np.clip(inits_array, 1e-4, None)
    
    # Phase 1: Massive parallel search
    x, R = batched_adam(inits_array, lr=0.05, num_iters=200)
    idx = np.argsort(R)[-1000:]
    x = x[idx]
    
    # Phase 2: Refinement
    x, R = batched_adam(x, lr=0.01, num_iters=500)
    idx = np.argsort(R)[-100:]
    x = x[idx]
    
    # Phase 3: Deep optimization
    x, R = batched_adam(x, lr=0.002, num_iters=2000)
    idx = np.argsort(R)[-10:]
    x = x[idx]
    
    # Phase 4: Refinement, Mass Swaps, and Final Polish
    def obj_and_grad_scipy(x_single):
        x_batch = x_single[None, :]
        R_val, dx = compute_obj_and_grad_batch(x_batch)
        return -float(R_val[0]), -dx[0].astype(np.float64)

    best_final_x = None
    best_final_R = -1.0
    
    for i in range(len(x)):
        # 1. Initial Continuous Convergence
        res = minimize(
            obj_and_grad_scipy, 
            x[i], 
            method='L-BFGS-B', 
            jac=True, 
            options={'ftol': 1e-12, 'gtol': 1e-12, 'maxiter': 2000}
        )
        x_opt = res.x
        
        # 2. Discrete Mass Swaps to escape local optima
        h_opt = x_opt**2
        h_swapped, R_swapped = mass_swap_optimization(h_opt)
        
        # 3. Final Continuous Polish
        x_swapped = np.sqrt(np.maximum(h_swapped, 0.0))
        # Add a tiny epsilon to zeros to allow L-BFGS-B to escape spurious stationary points 
        # caused by the x^2 parameterization where gradients structurally vanish.
        x_swapped[x_swapped < 1e-8] = 1e-8
        
        res_final = minimize(
            obj_and_grad_scipy, 
            x_swapped, 
            method='L-BFGS-B', 
            jac=True, 
            options={'ftol': 1e-12, 'gtol': 1e-12, 'maxiter': 2000}
        )
        
        final_R_val = -res_final.fun
        if final_R_val > best_final_R:
            best_final_R = final_R_val
            best_final_x = res_final.x
            
        # Ensure we never lose the mass-swapped improvement due to L-BFGS-B drifting
        if R_swapped > best_final_R:
            best_final_R = R_swapped
            best_final_x = x_swapped

    # Construct final heights sequence (fully asymmetric, length 50)
    heights = (best_final_x**2).tolist()
    
    # Verify using exact baseline formula
    convolution = np.convolve(heights, heights)
    widths = np.diff(np.linspace(-0.5, 0.5, len(convolution) + 2))
    values = np.concatenate(([0.0], convolution, [0.0]))
    l2_squared = sum(
        widths[index] / 3.0 * (values[index] ** 2 + values[index] * values[index + 1] + values[index + 1] ** 2)
        for index in range(len(convolution) + 1)
    )
    l1 = float(np.sum(np.abs(convolution)) / (len(convolution) + 1))
    linf = float(np.max(np.abs(convolution)))
    
    final_R = float(l2_squared / (l1 * linf))
    
    return heights, final_R
# EVOLVE_END

if __name__ == "__main__":
    heights, c_lower_bound = optimize_lower_bound()
    print(json.dumps({"heights": heights, "c_lower_bound": c_lower_bound}))
```
