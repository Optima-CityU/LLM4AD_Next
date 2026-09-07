#!/usr/bin/env python3
"""Initial step function for the second autocorrelation inequality."""

import json
import numpy as np
from scipy.optimize import minimize, minimize_scalar

# EVOLVE_START
def optimize_lower_bound():
    """
    Optimizes the step function to maximize the second autocorrelation inequality lower bound.
    Uses a highly optimized, multi-phase hierarchical Adam optimizer with exact analytical gradients
    and LogSumExp relaxation for L_inf norm, interleaved with a Simulated Annealing based mass swap
    routine to escape local optima, and followed by L-BFGS-B for final polishing.
    """
    
    def compute_obj_and_grad_batch(x, beta=None):
        """
        Computes the objective R and its gradient with respect to x for a batch of inputs.
        x is the full sequence parameterization (length 50): h_i = x_i^2.
        """
        B = x.shape[0]
        h = x**2
        
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
        
        # Calculate L_inf norm
        if beta is None:
            Linf = np.max(c, axis=1)
            max_idx = np.argmax(c, axis=1)
            dc_Linf = np.zeros((B, 99))
            dc_Linf[np.arange(B), max_idx] = 1.0
        else:
            # LogSumExp relaxation for L_inf
            c_max = np.max(c, axis=1, keepdims=True)
            exp_c = np.exp(beta * (c - c_max))
            sum_exp_c = np.sum(exp_c, axis=1, keepdims=True)
            Linf = (c_max + np.log(sum_exp_c) / beta).squeeze(1)
            dc_Linf = exp_c / sum_exp_c
            
        # Objective R
        R = L2_sq / (L1 * Linf)
        
        # Gradients
        # 1. d L2_sq / d v
        dv = (np.roll(v, 1, axis=1) + 4*v + np.roll(v, -1, axis=1)) / 300.0
        dc_L2 = dv[:, 1:100] # valid for c_0 ... c_98
        
        # 2. d L1 / d c
        dc_L1 = np.ones((B, 99)) / 100.0
        
        # 3. d R / d c using quotient rule
        dc_R = (dc_L2 - (R * Linf)[:, None] * dc_L1 - (R * L1)[:, None] * dc_Linf) / (L1 * Linf)[:, None]
        
        # 4. d R / d h using cross-correlation
        DC_R = np.fft.rfft(dc_R, n=256, axis=1)
        H_conj = np.conj(np.fft.rfft(h, n=256, axis=1))
        dh_R_full = np.fft.irfft(DC_R * H_conj, n=256, axis=1)
        dh_R = 2 * dh_R_full[:, :50]
        
        # 5. d R / d x using chain rule
        dx_R = 2 * x * dh_R
        
        return R, dx_R

    def batched_adam(x_init, lr, num_iters, beta_start=None, beta_end=None):
        """Batched Adam optimizer for maximizing R with optional LogSumExp annealing."""
        x = x_init.copy()
        m = np.zeros_like(x)
        v = np.zeros_like(x)
        
        best_x = x.copy()
        best_R = np.full(x.shape[0], -1.0)
        
        beta1 = 0.9
        beta2 = 0.999
        eps = 1e-8
        
        for t in range(1, num_iters + 1):
            if beta_start is not None and beta_end is not None:
                alpha = (t - 1) / max(1, num_iters - 1)
                beta = beta_start * (beta_end / beta_start) ** alpha
            else:
                beta = None
                
            R, grad = compute_obj_and_grad_batch(x, beta=beta)
            
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
        L2_sq = (2.0 * np.sum(c**2) + np.sum(c[:-1] * c[1:])) / 300.0
        L1 = np.sum(c) / 100.0
        Linf = np.max(c)
        if L1 == 0 or Linf == 0:
            return 0.0
        return L2_sq / (L1 * Linf)

    def mass_swap_optimization(h_init):
        """
        Performs discrete local-optima-escaping mass swaps with Simulated Annealing, 
        incorporating block-level mutations (uniformization, adjacent merge, and mass transfer) 
        to explicitly bias the search towards piecewise constant structures, 
        followed by exact 1D bounded line searches.
        """
        h = h_init.copy()
        best_R = exact_objective_h(h)
        best_h = h.copy()
        
        # SA parameters
        T_start = 1e-4
        T_end = 1e-7
        sa_iters = 15000  # Increased to allow thorough exploration with new block operators
        
        current_R = best_R
        
        for step in range(sa_iters):
            T = T_start * (T_end / T_start) ** (step / max(1, sa_iters - 1))
            
            h_new = h.copy()
            op = np.random.rand()
            
            if op < 0.20:
                # 2-way swap
                i, j = np.random.choice(50, 2, replace=False)
                if h_new[i] < 1e-9 and h_new[j] < 1e-9:
                    continue
                delta = np.random.uniform(-h_new[i], h_new[j])
                if np.random.rand() < 0.8:
                    delta *= np.random.uniform(0.01, 0.2) # favor smaller local moves
                h_new[i] += delta
                h_new[j] -= delta
                
            elif op < 0.40:
                # 3-way swap
                i, j, k = np.random.choice(50, 3, replace=False)
                total_mass = h_new[i] + h_new[j] + h_new[k]
                if total_mass < 1e-9:
                    continue
                if np.random.rand() < 0.5:
                    r1, r2 = np.random.rand(2)
                    if r1 + r2 > 1.0:
                        r1, r2 = 1.0 - r1, 1.0 - r2
                    r3 = 1.0 - r1 - r2
                    h_new[i] = total_mass * r1
                    h_new[j] = total_mass * r2
                    h_new[k] = total_mass * r3
                else:
                    transfer = np.random.uniform(0, 0.2 * total_mass)
                    transfer = min(transfer, h_new[i])
                    h_new[i] -= transfer
                    split = np.random.rand()
                    h_new[j] += transfer * split
                    h_new[k] += transfer * (1 - split)
                    
            elif op < 0.60:
                # Block uniformization: averages the heights of a randomly selected contiguous interval
                i = np.random.randint(0, 50)
                j = np.random.randint(i, 50)
                if j > i:
                    avg = np.mean(h_new[i:j+1])
                    h_new[i:j+1] = avg
                    
            elif op < 0.80:
                # Block mass transfer: shifts mass between two randomly chosen intervals
                L1 = np.random.randint(1, 15)
                L2 = np.random.randint(1, 15)
                i1 = np.random.randint(0, 50 - L1 + 1)
                i2 = np.random.randint(0, 50 - L2 + 1)
                
                min_val = np.min(h_new[i1:i1+L1])
                if min_val > 1e-9:
                    # Shift a random amount of mass (up to 100% of the minimum height in block 1)
                    delta1 = np.random.uniform(0.01, 1.0) * min_val
                    total_transfer = delta1 * L1
                    delta2 = total_transfer / L2
                    
                    h_new[i1:i1+L1] -= delta1
                    h_new[i2:i2+L2] += delta2
                    
            else:
                # Adjacent block merge: explicitly enforces piecewise constant structures
                if np.random.rand() < 0.5:
                    i = np.random.randint(0, 49)
                    h_new[i+1] = h_new[i]
                else:
                    i = np.random.randint(1, 50)
                    h_new[i-1] = h_new[i]
            
            # Ensure no negative values due to floating point inaccuracies
            h_new = np.maximum(0.0, h_new)
            new_R = exact_objective_h(h_new)
            
            # SA acceptance criterion
            if new_R > current_R or np.random.rand() < np.exp((new_R - current_R) / T):
                h = h_new
                current_R = new_R
                if current_R > best_R:
                    best_R = current_R
                    best_h = h.copy()
                    
        # Greedy polish with 1D line searches
        h = best_h.copy()
        improved = True
        sweep_count = 0
        while improved and sweep_count < 15:
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
                    
                    deltas = np.linspace(-h[j], h[i], 15)
                    vals = np.array([obj_delta(d) for d in deltas])
                    min_idx = np.argmin(vals)
                    min_val = vals[min_idx]
                    best_d = deltas[min_idx]
                    
                    if -min_val > best_R - 1e-5:
                        res = minimize_scalar(
                            obj_delta, 
                            bounds=(-h[j], h[i]), 
                            method='bounded', 
                            options={'xatol': 1e-9, 'maxiter': 30}
                        )
                        
                        cand_val = -res.fun if res.success else -np.inf
                        cand_d = res.x if res.success else 0.0
                        
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

    # Generate diverse structured and random initializations
    inits = []
    inits.append(np.ones(50)) # Constant
    for c in np.linspace(0, 1, 20): # Linear
        inits.append(np.linspace(1, 1-c, 50))
        inits.append(np.linspace(1-c, 1, 50))
    for c in np.linspace(0, 1, 20): # Quadratic
        inits.append(1 - c * np.linspace(0, 1, 50)**2)
        inits.append(1 - c * np.linspace(1, 0, 50)**2)
        inits.append(1 - c * np.linspace(-1, 1, 50)**2)
    for k in range(1, 50): # Step functions
        for v in np.linspace(0.0, 0.9, 10):
            x = np.ones(50)
            x[k:] = v
            inits.append(x)
            x_rev = np.ones(50)
            x_rev[:k] = v
            inits.append(x_rev)
    for freq in np.linspace(0.1, 5, 20): # Sine waves
        inits.append(np.abs(np.sin(freq * np.linspace(0, 1, 50))))
        inits.append(np.abs(np.cos(freq * np.linspace(0, 1, 50))))
        
    # 2-step functions (more granular structural search)
    for k1 in range(1, 48, 3):
        for k2 in range(k1+1, 49, 3):
            for v1 in [0.0, 0.25, 0.5, 0.75]:
                for v2 in [0.0, 0.25, 0.5, 0.75]:
                    x = np.ones(50)
                    x[k1:k2] = v1
                    x[k2:] = v2
                    inits.append(x)
                    
    np.random.seed(42)
    num_random = max(0, 25000 - len(inits))
    
    # Mix standard random initializations with blocky random initializations
    # to seed the continuous optimizer with piecewise constant structures
    random_inits = np.zeros((num_random, 50))
    for i in range(num_random):
        if np.random.rand() < 0.5:
            random_inits[i] = np.random.rand(50)
        else:
            num_blocks = np.random.randint(2, 10)
            indices = np.sort(np.random.choice(49, num_blocks - 1, replace=False)) + 1
            indices = np.concatenate(([0], indices, [50]))
            for b in range(num_blocks):
                random_inits[i, indices[b]:indices[b+1]] = np.random.rand()
                
    inits_array = np.vstack([np.array(inits), random_inits])
    # Ensure strictly positive initialization
    inits_array = np.clip(inits_array, 1e-4, None)
    
    # Phase 1: Massive parallel search
    x, R = batched_adam(inits_array, lr=0.05, num_iters=300, beta_start=1.0, beta_end=100.0)
    idx = np.argsort(R)[-1000:]
    x = x[idx]
    
    # Phase 2: Refinement
    x, R = batched_adam(x, lr=0.01, num_iters=600, beta_start=100.0, beta_end=1000.0)
    idx = np.argsort(R)[-100:]
    x = x[idx]
    
    # Phase 3: Deep optimization (with a high beta to smooth out the exact max ridge)
    x, R = batched_adam(x, lr=0.002, num_iters=2500, beta_start=1000.0, beta_end=5000.0)
    idx = np.argsort(R)[-10:]
    x = x[idx]
    
    # Phase 4: Refinement, Mass Swaps, and Final Polish
    def obj_and_grad_scipy(x_single):
        x_batch = x_single[None, :]
        R_val, dx = compute_obj_and_grad_batch(x_batch, beta=None)
        return -float(R_val[0]), -dx[0].astype(np.float64)

    best_final_x = None
    best_final_R = -1.0
    
    for i in range(len(x)):
        x_opt = x[i]
        
        # Iteratively apply continuous convergence and discrete mass swaps
        # This cycle allows the continuous optimizer to polish the result of the mass swap,
        # and then pass it back for further structural improvements.
        for cycle in range(2):
            # 1. Continuous Convergence
            res = minimize(
                obj_and_grad_scipy, 
                x_opt, 
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
            x_opt = res_final.x
            
            final_R_val = -res_final.fun
            if final_R_val > best_final_R:
                best_final_R = final_R_val
                best_final_x = res_final.x
                
            # Ensure we never lose the mass-swapped improvement due to L-BFGS-B drifting
            if R_swapped > best_final_R:
                best_final_R = R_swapped
                best_final_x = x_swapped

    # Construct final heights sequence
    x2 = best_final_x**2
    heights = x2.tolist()
    
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