Insight from a high-scoring run on 21-circle packing (perimeter ≤ 4).

- Broad-Search Bootstrapped Top-K ILS: When packing 21 circles under a rectangle perimeter ≤ 4, combining a broad array of hexagonal staggered partitions with dense aspect ratio perturbations and then applying Top-K filtering before any epsilon-annealed SLSQP polish, while embedding a fixed-center LP bootstrap inside a dynamic trust-region LP to pre-inflate radii, produced a valid layout (validity 1.0) with sum_radii 2.3658323757008346. Reuse this staged pipeline—Top-K filtered seed generation → fixed-center LP bootstrap refinement → brief epsilon-annealed SLSQP polish → Simulated Annealing ILS with small center perturbations and short LP bursts—to efficiently explore diverse contact graphs and extract microscopic slack without destabilizing the optimizer under tight perimeter constraints.

```python
#!/usr/bin/env python3
"""Initial candidate for packing 21 circles in a perimeter-four rectangle."""

import json

import numpy as np


# EVOLVE_START
from scipy.optimize import linprog, minimize

def get_natural_wh(pattern):
    """Calculate the natural aspect ratio for a hexagonal staggered pattern."""
    max_cols = max(pattern)
    num_rows = len(pattern)
    width = max(1, max_cols - 1) * 1.0
    height = max(1, num_rows - 1) * (3**0.5 / 2)
    # Scale so that width + height = 2 (perimeter = 4)
    scale = 2.0 / (width + height)
    return width * scale, height * scale

def generate_seeds(num_circles):
    """Generate various seed configurations to explore different contact graphs."""
    base_patterns = [
        [5, 5, 5, 3, 3],
        [6, 4, 4, 4, 3],
        [5, 4, 5, 4, 3],
        [6, 5, 5, 5],
        [4, 5, 4, 4, 4],
        [7, 7, 7],
        [3, 4, 5, 5, 4],
        [4, 4, 5, 4, 4],
        [5, 5, 6, 5],
        [7, 6, 8]
    ]
    
    unique_patterns = []
    for p in base_patterns:
        if sum(p) != num_circles:
            continue
        if p not in unique_patterns:
            unique_patterns.append(p)
        p_rev = p[::-1]
        if p_rev not in unique_patterns:
            unique_patterns.append(p_rev)
            
    # Fallback if num_circles is not 21
    if not unique_patterns:
        grid_size = int(np.ceil(np.sqrt(num_circles)))
        p = [grid_size] * (num_circles // grid_size)
        if num_circles % grid_size != 0:
            p.append(num_circles % grid_size)
        unique_patterns.append(p)
            
    seeds = []
    
    for p in unique_patterns:
        nat_w, nat_h = get_natural_wh(p)
        
        # Perturb aspect ratios
        for w_mult in [0.97, 1.0, 1.03]:
            w_scale = nat_w * w_mult
            h_scale = 2.0 - w_scale
            if w_scale <= 0 or h_scale <= 0:
                continue
                
            centers = []
            num_rows = len(p)
            for i, count in enumerate(p):
                y = (i / max(1, num_rows - 1)) * h_scale if num_rows > 1 else h_scale / 2.0
                for j in range(count):
                    offset = (max(p) - count) / 2.0
                    x = ((j + offset) / max(1, max(p) - 1)) * w_scale if max(p) > 1 else w_scale / 2.0
                    centers.append([x, y])
            centers = np.array(centers)
            
            # Nominal aspect ratio seed
            seeds.append(centers)
            # Transposed version
            seeds.append(centers[:, [1, 0]])
            
            # Deterministic, unequal-radius jittered seeds to break symmetry
            p_int = sum(x * (10**i) for i, x in enumerate(p))
            rng = np.random.RandomState(p_int + int(w_mult*100))
            jitter = rng.uniform(-0.01, 0.01, size=centers.shape)
            
            seeds.append(centers + jitter)
            seeds.append(centers[:, [1, 0]] + jitter)
            
    return seeds

def lp_refine(centers, iterations=15, fixed_bootstrap=True):
    """
    Iteratively solve a Linear Program to maximize the sum of radii.
    Non-overlap constraints are conservatively linearized based on previous centers.
    A dynamic trust-region constraint restricts center displacements per iteration
    to keep the linear approximation valid and prevent oscillation.
    """
    N = len(centers)
    radii = np.full(N, 0.01)
    W, H = 1.0, 1.0
    
    num_constraints = 1 + 4*N + N*(N-1)//2
    num_vars = 3*N + 2
    
    c = np.zeros(num_vars)
    c[2*N : 3*N] = -1.0
    
    best_score = -1.0
    
    # Optional fixed-center bootstrap to optimally inflate radii and consume initial geometric slack
    if fixed_bootstrap:
        A_ub = np.zeros((num_constraints, num_vars))
        b_ub = np.zeros(num_constraints)
        bounds = []
        for i in range(N):
            cx = centers[i, 0]
            bounds.append((cx, cx))
        for i in range(N):
            cy = centers[i, 1]
            bounds.append((cy, cy))
        bounds.extend([(0.0, 1.0)] * N)
        bounds.extend([(0.0, 2.0), (0.0, 2.0)])
        
        row_idx = 0
        A_ub[row_idx, -2] = 1.0; A_ub[row_idx, -1] = 1.0; b_ub[row_idx] = 2.0; row_idx += 1
        for i in range(N):
            A_ub[row_idx, i] = -1.0; A_ub[row_idx, 2*N + i] = 1.0; row_idx += 1
            A_ub[row_idx, i] = 1.0; A_ub[row_idx, 2*N + i] = 1.0; A_ub[row_idx, -2] = -1.0; row_idx += 1
            A_ub[row_idx, N + i] = -1.0; A_ub[row_idx, 2*N + i] = 1.0; row_idx += 1
            A_ub[row_idx, N + i] = 1.0; A_ub[row_idx, 2*N + i] = 1.0; A_ub[row_idx, -1] = -1.0; row_idx += 1
        for i in range(N):
            for j in range(i+1, N):
                dx = centers[i, 0] - centers[j, 0]
                dy = centers[i, 1] - centers[j, 1]
                dist = np.hypot(dx, dy)
                if dist < 1e-9:
                    nx, ny = 1.0, 0.0
                else:
                    nx, ny = dx / dist, dy / dist
                A_ub[row_idx, i] = -nx; A_ub[row_idx, N + i] = -ny
                A_ub[row_idx, j] = nx; A_ub[row_idx, N + j] = ny
                A_ub[row_idx, 2*N + i] = 1.0; A_ub[row_idx, 2*N + j] = 1.0; row_idx += 1
        res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method='highs')
        if res.success:
            centers = np.column_stack((res.x[:N], res.x[N:2*N]))
            radii = res.x[2*N:3*N]
            W = res.x[-2]
            H = res.x[-1]

    for it in range(iterations):
        A_ub = np.zeros((num_constraints, num_vars))
        b_ub = np.zeros(num_constraints)
        
        trust_region = 0.05 * (0.95 ** it)
        bounds = []
        
        for i in range(N):
            cx = centers[i, 0]
            bounds.append((max(0.0, cx - trust_region), min(2.0, cx + trust_region)))
        for i in range(N):
            cy = centers[i, 1]
            bounds.append((max(0.0, cy - trust_region), min(2.0, cy + trust_region)))
        bounds.extend([(0.0, 1.0)] * N)
        bounds.extend([(0.0, 2.0), (0.0, 2.0)])
        
        row_idx = 0
        A_ub[row_idx, -2] = 1.0; A_ub[row_idx, -1] = 1.0; b_ub[row_idx] = 2.0; row_idx += 1
        for i in range(N):
            A_ub[row_idx, i] = -1.0; A_ub[row_idx, 2*N + i] = 1.0; row_idx += 1
            A_ub[row_idx, i] = 1.0; A_ub[row_idx, 2*N + i] = 1.0; A_ub[row_idx, -2] = -1.0; row_idx += 1
            A_ub[row_idx, N + i] = -1.0; A_ub[row_idx, 2*N + i] = 1.0; row_idx += 1
            A_ub[row_idx, N + i] = 1.0; A_ub[row_idx, 2*N + i] = 1.0; A_ub[row_idx, -1] = -1.0; row_idx += 1
            
        for i in range(N):
            for j in range(i+1, N):
                dx = centers[i, 0] - centers[j, 0]
                dy = centers[i, 1] - centers[j, 1]
                dist = np.hypot(dx, dy)
                if dist < 1e-9:
                    nx, ny = 1.0, 0.0
                else:
                    nx, ny = dx / dist, dy / dist
                A_ub[row_idx, i] = -nx; A_ub[row_idx, N + i] = -ny
                A_ub[row_idx, j] = nx; A_ub[row_idx, N + j] = ny
                A_ub[row_idx, 2*N + i] = 1.0; A_ub[row_idx, 2*N + j] = 1.0; row_idx += 1
                
        res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method='highs')
        if res.success:
            centers = np.column_stack((res.x[:N], res.x[N:2*N]))
            radii = res.x[2*N:3*N]
            W = res.x[-2]
            H = res.x[-1]
            best_score = np.sum(radii)
        else:
            break
            
    return centers, radii, W, H, best_score

def nlp_polish(centers, radii, W, H, epsilon=1e-6):
    """
    Nonlinear polishing using SLSQP with exact squared-distance non-overlap constraints.
    Includes pinning boundary-contact circles to eliminate rigid modes.
    """
    N = len(centers)
    
    def objective(vars):
        return -np.sum(vars[2*N : 3*N])
        
    def objective_jac(vars):
        jac = np.zeros_like(vars)
        jac[2*N : 3*N] = -1.0
        return jac

    constraints = []
    
    constraints.append({
        'type': 'ineq',
        'fun': lambda v: 2.0 - v[-2] - v[-1],
        'jac': lambda v: np.concatenate([np.zeros(3*N), [-1.0, -1.0]])
    })
    
    idx_left = np.argmin(centers[:, 0] - radii)
    idx_right = np.argmax(centers[:, 0] + radii)
    idx_bottom = np.argmin(centers[:, 1] - radii)
    idx_top = np.argmax(centers[:, 1] + radii)
    
    idx_x_min = [i for i in range(N) if i != idx_left]
    def box_x_min(v): return v[idx_x_min] - v[2*N:3*N][idx_x_min]
    def box_x_min_jac(v):
        J = np.zeros((len(idx_x_min), len(v)))
        for k, i in enumerate(idx_x_min):
            J[k, i] = 1.0
            J[k, 2*N + i] = -1.0
        return J
    constraints.append({'type': 'ineq', 'fun': box_x_min, 'jac': box_x_min_jac})
    
    idx_x_max = [i for i in range(N) if i != idx_right]
    def box_x_max(v): return v[-2] - v[idx_x_max] - v[2*N:3*N][idx_x_max]
    def box_x_max_jac(v):
        J = np.zeros((len(idx_x_max), len(v)))
        for k, i in enumerate(idx_x_max):
            J[k, -2] = 1.0
            J[k, i] = -1.0
            J[k, 2*N + i] = -1.0
        return J
    constraints.append({'type': 'ineq', 'fun': box_x_max, 'jac': box_x_max_jac})
    
    idx_y_min = [i for i in range(N) if i != idx_bottom]
    def box_y_min(v): return v[N + np.array(idx_y_min)] - v[2*N:3*N][idx_y_min]
    def box_y_min_jac(v):
        J = np.zeros((len(idx_y_min), len(v)))
        for k, i in enumerate(idx_y_min):
            J[k, N + i] = 1.0
            J[k, 2*N + i] = -1.0
        return J
    constraints.append({'type': 'ineq', 'fun': box_y_min, 'jac': box_y_min_jac})
    
    idx_y_max = [i for i in range(N) if i != idx_top]
    def box_y_max(v): return v[-1] - v[N + np.array(idx_y_max)] - v[2*N:3*N][idx_y_max]
    def box_y_max_jac(v):
        J = np.zeros((len(idx_y_max), len(v)))
        for k, i in enumerate(idx_y_max):
            J[k, -1] = 1.0
            J[k, N + i] = -1.0
            J[k, 2*N + i] = -1.0
        return J
    constraints.append({'type': 'ineq', 'fun': box_y_max, 'jac': box_y_max_jac})
    
    pairs = [(i, j) for i in range(N) for j in range(i+1, N)]
    def non_overlap(v):
        x = v[:N]
        y = v[N:2*N]
        r = v[2*N:3*N]
        vals = np.zeros(len(pairs))
        for idx, (i, j) in enumerate(pairs):
            dx = x[i] - x[j]
            dy = y[i] - y[j]
            R = r[i] + r[j] + epsilon
            vals[idx] = dx*dx + dy*dy - R*R
        return vals
        
    def non_overlap_jac(v):
        J = np.zeros((len(pairs), len(v)))
        x = v[:N]
        y = v[N:2*N]
        r = v[2*N:3*N]
        for idx, (i, j) in enumerate(pairs):
            dx = x[i] - x[j]
            dy = y[i] - y[j]
            R = r[i] + r[j] + epsilon
            
            J[idx, i] = 2 * dx
            J[idx, j] = -2 * dx
            J[idx, N + i] = 2 * dy
            J[idx, N + j] = -2 * dy
            J[idx, 2*N + i] = -2 * R
            J[idx, 2*N + j] = -2 * R
        return J
        
    constraints.append({'type': 'ineq', 'fun': non_overlap, 'jac': non_overlap_jac})
    
    def pin_left(v): return v[idx_left] - v[2*N + idx_left]
    def pin_left_jac(v):
        J = np.zeros(len(v))
        J[idx_left] = 1.0; J[2*N + idx_left] = -1.0
        return J
    constraints.append({'type': 'eq', 'fun': pin_left, 'jac': pin_left_jac})
    
    def pin_right(v): return v[-2] - v[idx_right] - v[2*N + idx_right]
    def pin_right_jac(v):
        J = np.zeros(len(v))
        J[-2] = 1.0; J[idx_right] = -1.0; J[2*N + idx_right] = -1.0
        return J
    constraints.append({'type': 'eq', 'fun': pin_right, 'jac': pin_right_jac})
    
    def pin_bottom(v): return v[N + idx_bottom] - v[2*N + idx_bottom]
    def pin_bottom_jac(v):
        J = np.zeros(len(v))
        J[N + idx_bottom] = 1.0; J[2*N + idx_bottom] = -1.0
        return J
    constraints.append({'type': 'eq', 'fun': pin_bottom, 'jac': pin_bottom_jac})
    
    def pin_top(v): return v[-1] - v[N + idx_top] - v[2*N + idx_top]
    def pin_top_jac(v):
        J = np.zeros(len(v))
        J[-1] = 1.0; J[N + idx_top] = -1.0; J[2*N + idx_top] = -1.0
        return J
    constraints.append({'type': 'eq', 'fun': pin_top, 'jac': pin_top_jac})
    
    bounds = [(0, 2)] * (2*N) + [(0, 1)] * N + [(0, 2), (0, 2)]
    x0 = np.concatenate([centers[:, 0], centers[:, 1], radii, [W, H]])
    
    res = minimize(objective, x0, method='SLSQP', jac=objective_jac,
                   bounds=bounds, constraints=constraints,
                   options={'maxiter': 200, 'ftol': 1e-9})
                   
    if res.success:
        out_centers = np.column_stack((res.x[:N], res.x[N:2*N]))
        out_radii = res.x[2*N:3*N]
        out_W = res.x[-2]
        out_H = res.x[-1]
        return out_centers, out_radii, out_W, out_H, -res.fun
    else:
        return centers, radii, W, H, np.sum(radii)

def finalize(centers, radii):
    """Recompute exactly, apply uniform infinitesimal safety reduction, and shift to origin."""
    min_x = np.min(centers[:, 0] - radii)
    max_x = np.max(centers[:, 0] + radii)
    min_y = np.min(centers[:, 1] - radii)
    max_y = np.max(centers[:, 1] + radii)
    
    centers[:, 0] -= min_x
    centers[:, 1] -= min_y
    
    W = max_x - min_x
    H = max_y - min_y
    
    radii -= 1e-11
    
    perimeter = W + H
    if perimeter > 2.0:
        scale = 2.0 / perimeter
        centers *= scale
        radii *= scale
        radii -= 1e-12
        
    return np.column_stack((centers, radii))

def sa_ils_loop(centers, radii, W, H, iterations=20):
    """Simulated Annealing Iterated Local Search (ILS) loop."""
    best_centers, best_radii, best_W, best_H = centers, radii, W, H
    best_score = np.sum(radii)
    
    curr_centers, curr_radii, curr_W, curr_H = centers, radii, W, H
    curr_score = best_score
    
    T = 1e-3
    alpha = 0.85
    
    for _ in range(iterations):
        jitter = np.random.uniform(-0.005, 0.005, size=curr_centers.shape)
        new_centers = curr_centers + jitter
        
        # Short burst of bootstrapped LP refinement
        new_centers, new_radii, new_W, new_H, _ = lp_refine(new_centers, iterations=10, fixed_bootstrap=True)
        
        # Annealed NLP polishing
        new_centers, new_radii, new_W, new_H, new_score = nlp_polish(
            new_centers, new_radii, new_W, new_H, epsilon=1e-10
        )
        
        if new_score > curr_score or np.exp((new_score - curr_score) / T) > np.random.rand():
            curr_centers, curr_radii, curr_W, curr_H = new_centers, new_radii, new_W, new_H
            curr_score = new_score
            if new_score > best_score:
                best_centers, best_radii, best_W, best_H = curr_centers, curr_radii, curr_W, curr_H
                best_score = new_score
                
        T *= alpha
        
    return best_centers, best_radii, best_W, best_H

def construct_packing(num_circles: int = 21):
    seeds = generate_seeds(num_circles)
    
    # Phase 1: Broad multi-start exploration using LP with 30 iterations
    evaluated_seeds = []
    for seed in seeds:
        centers, radii, W, H, score = lp_refine(seed, iterations=30, fixed_bootstrap=True)
        if score > 0:
            evaluated_seeds.append((score, centers, radii, W, H))
            
    if not evaluated_seeds:
        # Emergency fallback
        radius = 0.01
        centers = np.array(
            [[(column + 0.5) / 5, (row + 0.5) / 5] for row in range(5) for column in range(5)][:num_circles],
            dtype=float,
        )
        radii = np.full(num_circles, radius, dtype=float)
        return np.column_stack((centers, radii))
        
    evaluated_seeds.sort(key=lambda x: x[0], reverse=True)
    
    # Phase 2: Epsilon-annealed NLP polishing for the top 5 candidates
    top_5 = evaluated_seeds[:5]
    best_score = -1.0
    best_layout = None
    
    eps_schedule = [1e-6, 1e-8, 1e-10, 1e-12]
    
    polished_candidates = []
    for score, centers, radii, W, H in top_5:
        curr_centers, curr_radii, curr_W, curr_H = centers, radii, W, H
        
        for eps in eps_schedule:
            curr_centers, curr_radii, curr_W, curr_H, _ = nlp_polish(
                curr_centers, curr_radii, curr_W, curr_H, epsilon=eps
            )
            
        final_score = np.sum(curr_radii)
        polished_candidates.append((final_score, curr_centers, curr_radii, curr_W, curr_H))
            
    polished_candidates.sort(key=lambda x: x[0], reverse=True)
    
    # Phase 3: Simulated Annealing ILS loop on the absolute best configuration
    _, best_c, best_r, best_w, best_h = polished_candidates[0]
    final_centers, final_radii, _, _ = sa_ils_loop(best_c, best_r, best_w, best_h, iterations=15)
    
    return finalize(final_centers, final_radii)
# EVOLVE_END


if __name__ == "__main__":
    circles = construct_packing()
    print(json.dumps({"circles": circles.tolist()}))
```
