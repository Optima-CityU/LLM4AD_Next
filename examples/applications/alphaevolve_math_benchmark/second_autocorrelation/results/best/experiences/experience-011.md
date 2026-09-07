Insight from a successful run achieving c_lower_bound 0.9048716469808405 (validity 1.0) on the 50-interval step-function task.

- Cosine-Annealed Adam with Variable-Length Segment Search: Coupling a cosine-annealed learning rate in batched Adam (lr_t = lr * 0.5 * (1 + cos(pi*(t-1)/num_iters))) with a discrete segment_height_optimization that jointly tunes contiguous blocks of length 1–15 (using a denser 1D grid search) enabled rapid exploration followed by precise consolidation of wide, flat plateaus characteristic of the target step-function structure, yielding c_lower_bound 0.9048716469808405 with validity 1.0. Reuse this pattern when optimizing step-like landscapes: anneal the continuous optimizer for phase-wise exploration-to-exploitation, and interleave periodic joint segment-level re-optimizations to reshape plateaus in one shot, particularly where single-coordinate updates stall.

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
    Uses a highly optimized, multi-phase hierarchical Adam optimizer with exact analytical gradients.
    
    Algorithmic Innovations:
    1. Smooth Scale-Invariant L_inf Approximation: Replaces the non-smooth L_inf norm with a 
       scale-invariant Log-Sum-Exp function during continuous optimization. A continuation method 
       (increasing beta) allows L-BFGS-B to smoothly navigate ridges where multiple peaks balance.
    2. Direct Bounded Parameterization: Transitions to bounded h >= 0 space for L-BFGS-B optimization,
       completely eliminating the vanishing gradient problem at zero that traps x^2 parameterizations.
    3. Comprehensive Discrete Optimization Suite: Interleaves exact 1D bounded line searches for:
       - Segment Height Optimization (Explicitly targeting step-function structures of lengths 1 to 15)
       - Mass Swaps (Pairwise mass transfer to escape local maxima)
    4. Cosine Annealing Learning Rate: Introduces a cosine annealing schedule in the batched Adam
       optimizer for improved exploration and convergence.
    """
    
    def compute_obj_and_grad_batch_h(h, beta=None):
        """
        Computes the objective R and its gradient with respect to h for a batch of inputs.
        h is the full asymmetric sequence parameterization.
        If beta is provided, uses a smooth, scale-invariant approximation for L_inf.
        """
        B = h.shape[0]
        
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
        
        if beta is None:
            # Exact L_inf norm and subgradient
            max_c = np.max(c, axis=1, keepdims=True)
            Linf = max_c[:, 0]
            is_max = (c == max_c)
            dc_Linf = is_max / np.sum(is_max, axis=1, keepdims=True)
        else:
            # Smooth scale-invariant L_inf approximation using Log-Sum-Exp
            S = np.sum(c, axis=1, keepdims=True) + 1e-12
            z_c = c / S
            
            # To avoid overflow, subtract max(z_c)
            max_z = np.max(z_c, axis=1, keepdims=True)
            exp_bz = np.exp(beta * (z_c - max_z))
            E = np.sum(exp_bz, axis=1, keepdims=True)
            
            log_E_full = beta * max_z + np.log(E)
            Linf = (S * log_E_full / beta)[:, 0]
            
            # Gradients of the smooth L_inf
            p = exp_bz / E
            z_p_sum = np.sum(z_c * p, axis=1, keepdims=True)
            dc_Linf = log_E_full / beta + p - z_p_sum
        
        # Objective R
        R = L2_sq / (L1 * Linf)
        
        # Gradients
        # 1. d L2_sq / d v
        dv = (np.roll(v, 1, axis=1) + 4*v + np.roll(v, -1, axis=1)) / 300.0
        dc_L2 = dv[:, 1:100] # valid for c_0 ... c_98
        
        # 2. d L1 / d c
        dc_L1 = np.ones((B, 99)) / 100.0
        
        # 4. d R / d c using quotient rule
        dc_R = (dc_L2 - (R * Linf)[:, None] * dc_L1 - (R * L1)[:, None] * dc_Linf) / (L1 * Linf)[:, None]
        
        # 5. d R / d h using cross-correlation
        DC_R = np.fft.rfft(dc_R, n=256, axis=1)
        H_conj = np.conj(np.fft.rfft(h, n=256, axis=1))
        dh_R_full = np.fft.irfft(DC_R * H_conj, n=256, axis=1)
        dh_R = 2 * dh_R_full[:, :50]
        
        return R, dh_R

    def compute_obj_and_grad_batch_x(x, beta=None):
        """Wrapper for unconstrained x^2 parameterization used in initial Adam phase."""
        R, dh = compute_obj_and_grad_batch_h(x**2, beta=beta)
        dx_R = 2 * x * dh
        return R, dx_R

    def batched_adam(x_init, lr, num_iters):
        """Batched Adam optimizer for maximizing R with Cosine Annealing."""
        x = x_init.copy()
        m = np.zeros_like(x)
        v = np.zeros_like(x)
        
        best_x = x.copy()
        best_R = np.full(x.shape[0], -1.0)
        
        beta1 = 0.9
        beta2 = 0.999
        eps = 1e-8
        
        for t in range(1, num_iters + 1):
            # Cosine annealing learning rate schedule
            lr_t = lr * 0.5 * (1.0 + np.cos(np.pi * (t - 1) / num_iters))
            
            R, grad = compute_obj_and_grad_batch_x(x, beta=None)
            
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
            
            x = x - lr_t * m_hat / (np.sqrt(v_hat) + eps)
            
        # Normalize best_x to prevent scale drift in subsequent stages
        best_x = best_x / np.max(np.abs(best_x), axis=1, keepdims=True)
        return best_x, best_R

    def exact_objective_h(h):
        """Highly optimized exact objective function for 1D line searches."""
        c = np.convolve(h, h)
        L2_sq = (2.0 * np.sum(c**2) + np.sum(c[:-1] * c[1:])) / 300.0
        L1 = np.sum(c) / 100.0
        Linf = np.max(c)
        if L1 == 0 or Linf == 0:
            return 0.0
        return L2_sq / (L1 * Linf)

    def segment_height_optimization(h_full):
        """Explicitly searches for step-function structures by evaluating and jointly optimizing contiguous segments of lengths 1 to 15."""
        h = h_full.copy()
        improved = True
        best_R = exact_objective_h(h)
        
        sweep_count = 0
        while improved and sweep_count < 10:
            improved = False
            sweep_count += 1
            
            for length in range(1, 16):
                for i in range(50 - length + 1):
                    def obj_segment(val):
                        h_temp = h.copy()
                        h_temp[i:i+length] = val
                        return -exact_objective_h(h_temp)
                    
                    avg_v = np.mean(h[i:i+length])
                    vals_test = np.linspace(0, max(avg_v*2, 1.0), 21)
                    objs = [obj_segment(v) for v in vals_test]
                    min_idx = np.argmin(objs)
                    best_v = vals_test[min_idx]
                    min_obj = objs[min_idx]
                    
                    res = minimize_scalar(
                        obj_segment,
                        bounds=(0.0, max(avg_v*3, 2.0)),
                        method='bounded',
                        options={'xatol': 1e-9, 'maxiter': 50}
                    )
                    
                    cand_val = -res.fun if res.success else -np.inf
                    cand_x = res.x if res.success else 0.0
                    
                    if -min_obj > cand_val:
                        cand_val = -min_obj
                        cand_x = best_v
                        
                    if cand_val > best_R + 1e-9:
                        best_R = cand_val
                        h[i:i+length] = cand_x
                        improved = True
        return h, best_R

    def mass_swap_optimization(h_full):
        """Transfers mass between pairs of elements to breakthrough continuous local maxima."""
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
                    
                    # Increased grid search resolution for robust basin identification
                    deltas = np.linspace(-h[j], h[i], 21)
                    vals = np.array([obj_delta(d) for d in deltas])
                    min_idx = np.argmin(vals)
                    min_val = vals[min_idx]
                    best_d = deltas[min_idx]
                    
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
                    
                    if -min_val > cand_val:
                        cand_val = -min_val
                        cand_d = best_d
                        
                    if cand_val > best_R + 1e-9:
                        best_R = cand_val
                        h[i] -= cand_d
                        h[j] += cand_d
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
    
    # Phase 4: Alternating Continuous and Discrete Optimization with Continuation
    def obj_and_grad_scipy_smooth_h(h_single, beta):
        h_batch = h_single[None, :]
        R_val, dh = compute_obj_and_grad_batch_h(h_batch, beta=beta)
        return -float(R_val[0]), -dh[0].astype(np.float64)

    def obj_and_grad_scipy_exact_h(h_single):
        h_batch = h_single[None, :]
        R_val, dh = compute_obj_and_grad_batch_h(h_batch, beta=None)
        return -float(R_val[0]), -dh[0].astype(np.float64)

    best_final_h = None
    best_final_R = -1.0
    bounds = [(0.0, None)] * 50
    
    for i in range(len(x)):
        h_opt = x[i]**2
        
        for alt_step in range(2):
            # 1. Continuous Convergence with Smooth Continuation
            # Gradually increase beta to transition from highly smooth to exactly non-smooth
            for beta in [1000, 5000, 20000, None]:
                obj_func = obj_and_grad_scipy_exact_h if beta is None else lambda h, b=beta: obj_and_grad_scipy_smooth_h(h, b)
                res = minimize(
                    obj_func, 
                    h_opt, 
                    method='L-BFGS-B', 
                    jac=True, 
                    bounds=bounds,
                    options={'ftol': 1e-12, 'gtol': 1e-12, 'maxiter': 1000}
                )
                h_opt = res.x
                
            # 2. Discrete Optimization Suite
            h_opt, _ = segment_height_optimization(h_opt)
            h_opt, R_discrete = mass_swap_optimization(h_opt)
            
            # Normalize to prevent scale drift and re-parameterize
            h_opt = h_opt / (np.max(h_opt) + 1e-12)
            
            if R_discrete > best_final_R:
                best_final_R = R_discrete
                best_final_h = h_opt.copy()
                
        # Final polish for this candidate
        res_final = minimize(
            obj_and_grad_scipy_exact_h, 
            h_opt, 
            method='L-BFGS-B', 
            jac=True, 
            bounds=bounds,
            options={'ftol': 1e-12, 'gtol': 1e-12, 'maxiter': 2000}
        )
        
        final_R_val = -res_final.fun
        if final_R_val > best_final_R:
            best_final_R = final_R_val
            best_final_h = res_final.x

    # Construct final heights sequence (fully asymmetric, length 50)
    heights = best_final_h.tolist()
    
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
