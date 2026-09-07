Actionable insight distilled from the algorithm’s design, metrics, and implementation for maximizing the minimum triangle area in an equilateral triangle.

- Three-Phase Active-Set Adam Optimization: It uses a batch size of 4000 with Adam and strict projection to (u, v) simplex constraints to keep all points strictly inside the triangle, which corresponded to validity 1.0 in evaluation.
- Three-Phase Active-Set Adam Optimization: Phase 2 replaces the annealed soft-min surrogate with an active-set subgradient that averages gradients from all triangles within 1e-5 of the current minimum area, preventing oscillation between competing worst triangles and sharpening the ascent direction for the true max–min objective.
- Three-Phase Active-Set Adam Optimization: A final fine-tuning phase tightens the active-set tolerance to 1e-7 and lowers the learning rate over 400 iterations to consolidate convergence to a local max–min equilibrium.

```python
#!/usr/bin/env python3
"""Heilbronn problem solver using batched gradient ascent with Adam."""

import json
import itertools
import math
import random
import numpy as np


# EVOLVE_START
def find_best_placement(n):
    B = 4000  # Batch size
    ITERS_PHASE1 = 1000
    ITERS_PHASE2 = 800
    ITERS_PHASE3 = 400
    lr = 0.01
    margin = 1e-5
    
    # Initialize random points in (u, v) space such that u > 0, v > 0, u+v < 1
    # We use (u, v) coordinates where the triangle is bounded by (0,0), (1,0), (0,1)
    np.random.seed(42)
    u = np.random.rand(B, n)
    v = np.random.rand(B, n)
    mask = u + v > 1.0
    u[mask], v[mask] = 1.0 - u[mask], 1.0 - v[mask]
    
    P = np.stack([u, v], axis=-1)  # (B, n, 2)
    
    # Adam parameters
    m = np.zeros_like(P)
    v_adam = np.zeros_like(P)
    beta1 = 0.9
    beta2 = 0.999
    epsilon = 1e-8
    
    # Precompute combinations
    combs = list(itertools.combinations(range(n), 3))
    idx0 = [c[0] for c in combs]
    idx1 = [c[1] for c in combs]
    idx2 = [c[2] for c in combs]
    num_combs = len(combs)
    
    def get_areas(P_batch):
        # P_batch: (B, n, 2)
        p0 = P_batch[:, idx0, :]
        p1 = P_batch[:, idx1, :]
        p2 = P_batch[:, idx2, :]
        
        # Cross product to find area in (u,v) space
        # Area = 0.5 * abs((u1-u0)*(v2-v0) - (u2-u0)*(v1-v0))
        # Normalized area (relative to triangle of area 0.5) is exactly 2 * Area
        cross = (p1[:, :, 0] - p0[:, :, 0]) * (p2[:, :, 1] - p0[:, :, 1]) - \
                (p2[:, :, 0] - p0[:, :, 0]) * (p1[:, :, 1] - p0[:, :, 1])
        return np.abs(cross), cross
    
    def project(P_batch):
        # Enforce u >= margin, v >= margin, u+v <= 1 - margin
        # Simple iterative projection or clipping
        P_batch = np.clip(P_batch, margin, 1.0 - margin)
        # If u+v > 1 - margin, project onto the line u+v = 1-margin
        excess = P_batch[:, :, 0] + P_batch[:, :, 1] - (1.0 - margin)
        mask = excess > 0
        P_batch[mask, 0] -= excess[mask] / 2
        P_batch[mask, 1] -= excess[mask] / 2
        P_batch = np.clip(P_batch, margin, 1.0 - margin)
        return P_batch

    for step in range(1, ITERS_PHASE1 + ITERS_PHASE2 + ITERS_PHASE3 + 1):
        areas, cross = get_areas(P)  # areas: (B, C)
        
        # Gradients
        grad = np.zeros_like(P)
        
        if step <= ITERS_PHASE1:
            # Phase 1: Soft-min
            temp = 0.05 * (1.0 - step / ITERS_PHASE1) + 0.001
            # softmax weights for minimum (negative areas)
            weights = np.exp(-areas / temp)
            weights /= (np.sum(weights, axis=1, keepdims=True) + 1e-8)
        elif step <= ITERS_PHASE1 + ITERS_PHASE2:
            # Phase 2: Active-set subgradient
            min_areas = np.min(areas, axis=1, keepdims=True)
            active_mask = areas <= (min_areas + 1e-5)
            weights = active_mask.astype(float)
            weights /= (np.sum(weights, axis=1, keepdims=True) + 1e-8)
        else:
            # Phase 3: Fine-tuning with stricter active-set subgradient
            min_areas = np.min(areas, axis=1, keepdims=True)
            active_mask = areas <= (min_areas + 1e-7)
            weights = active_mask.astype(float)
            weights /= (np.sum(weights, axis=1, keepdims=True) + 1e-8)
            
        # We want to MAXIMIZE the area, so gradient is positive in direction of increasing area
        # cross is the signed area. The absolute area is abs(cross).
        # d(|x|)/dx = sign(x)
        signs = np.sign(cross)
        
        # For each combination, gradient with respect to p0, p1, p2
        # cross = (u1-u0)*(v2-v0) - (u2-u0)*(v1-v0)
        # d(cross)/du0 = -(v2-v0) + (v1-v0) = v1 - v2
        # d(cross)/dv0 = -(u1-u0) + (u2-u0) = u2 - u1
        # d(cross)/du1 = v2 - v0
        # d(cross)/dv1 = u0 - u2
        # d(cross)/du2 = v0 - v1
        # d(cross)/dv2 = u1 - u0
        
        p0 = P[:, idx0, :]
        p1 = P[:, idx1, :]
        p2 = P[:, idx2, :]
        
        dp0_u = p1[:, :, 1] - p2[:, :, 1]
        dp0_v = p2[:, :, 0] - p1[:, :, 0]
        dp1_u = p2[:, :, 1] - p0[:, :, 1]
        dp1_v = p0[:, :, 0] - p2[:, :, 0]
        dp2_u = p0[:, :, 1] - p1[:, :, 1]
        dp2_v = p1[:, :, 0] - p0[:, :, 0]
        
        # Multiply by sign and weight
        W = signs * weights  # (B, C)
        
        # Accumulate gradients
        # We have B batches, C combinations.
        # We can use np.add.at or a loop. Loop over C is 165, which is fast enough.
        grad_u = np.zeros((B, n))
        grad_v = np.zeros((B, n))
        
        for c in range(num_combs):
            w = W[:, c]
            i0, i1, i2 = idx0[c], idx1[c], idx2[c]
            
            grad_u[:, i0] += w * dp0_u[:, c]
            grad_v[:, i0] += w * dp0_v[:, c]
            
            grad_u[:, i1] += w * dp1_u[:, c]
            grad_v[:, i1] += w * dp1_v[:, c]
            
            grad_u[:, i2] += w * dp2_u[:, c]
            grad_v[:, i2] += w * dp2_v[:, c]
            
        grad = np.stack([grad_u, grad_v], axis=-1)
        
        # Adam update
        m = beta1 * m + (1 - beta1) * grad
        v_adam = beta2 * v_adam + (1 - beta2) * (grad ** 2)
        
        m_hat = m / (1 - beta1 ** step)
        v_hat = v_adam / (1 - beta2 ** step)
        
        P = P + lr * m_hat / (np.sqrt(v_hat) + epsilon)
        
        # Project
        P = project(P)
        
        # Decrease lr in phase 2 and phase 3
        if step == ITERS_PHASE1:
            lr *= 0.1
        elif step == ITERS_PHASE1 + ITERS_PHASE2:
            lr *= 0.1

    areas, _ = get_areas(P)
    min_areas = np.min(areas, axis=1)
    best_idx = np.argmax(min_areas)
    best_P = P[best_idx]
    best_min_area = min_areas[best_idx]
    
    # Convert back to (x, y)
    points = []
    for i in range(n):
        u, v = best_P[i]
        x = u + 0.5 * v
        y = math.sqrt(3.0) * 0.5 * v
        points.append([x, y])
        
    return np.array(points), best_min_area

def run_search_point(n=11):
    points, min_area = find_best_placement(n)
    return points.tolist(), float(min_area)
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))
```

```python
#!/usr/bin/env python3
"""Heilbronn problem solver using batched gradient ascent with Adam."""

import json
import itertools
import math
import random
import numpy as np


# EVOLVE_START
def find_best_placement(n):
    B = 4000  # Batch size
    ITERS_PHASE1 = 1000
    ITERS_PHASE2 = 800
    ITERS_PHASE3 = 400
    lr = 0.01
    margin = 1e-5
    
    # Initialize random points in (u, v) space such that u > 0, v > 0, u+v < 1
    # We use (u, v) coordinates where the triangle is bounded by (0,0), (1,0), (0,1)
    np.random.seed(42)
    u = np.random.rand(B, n)
    v = np.random.rand(B, n)
    mask = u + v > 1.0
    u[mask], v[mask] = 1.0 - u[mask], 1.0 - v[mask]
    
    P = np.stack([u, v], axis=-1)  # (B, n, 2)
    
    # Adam parameters
    m = np.zeros_like(P)
    v_adam = np.zeros_like(P)
    beta1 = 0.9
    beta2 = 0.999
    epsilon = 1e-8
    
    # Precompute combinations
    combs = list(itertools.combinations(range(n), 3))
    idx0 = [c[0] for c in combs]
    idx1 = [c[1] for c in combs]
    idx2 = [c[2] for c in combs]
    num_combs = len(combs)
    
    def get_areas(P_batch):
        # P_batch: (B, n, 2)
        p0 = P_batch[:, idx0, :]
        p1 = P_batch[:, idx1, :]
        p2 = P_batch[:, idx2, :]
        
        # Cross product to find area in (u,v) space
        # Area = 0.5 * abs((u1-u0)*(v2-v0) - (u2-u0)*(v1-v0))
        # Normalized area (relative to triangle of area 0.5) is exactly 2 * Area
        cross = (p1[:, :, 0] - p0[:, :, 0]) * (p2[:, :, 1] - p0[:, :, 1]) - \
                (p2[:, :, 0] - p0[:, :, 0]) * (p1[:, :, 1] - p0[:, :, 1])
        return np.abs(cross), cross
    
    def project(P_batch):
        # Enforce u >= margin, v >= margin, u+v <= 1 - margin
        # Simple iterative projection or clipping
        P_batch = np.clip(P_batch, margin, 1.0 - margin)
        # If u+v > 1 - margin, project onto the line u+v = 1-margin
        excess = P_batch[:, :, 0] + P_batch[:, :, 1] - (1.0 - margin)
        mask = excess > 0
        P_batch[mask, 0] -= excess[mask] / 2
        P_batch[mask, 1] -= excess[mask] / 2
        P_batch = np.clip(P_batch, margin, 1.0 - margin)
        return P_batch

    for step in range(1, ITERS_PHASE1 + ITERS_PHASE2 + ITERS_PHASE3 + 1):
        areas, cross = get_areas(P)  # areas: (B, C)
        
        # Gradients
        grad = np.zeros_like(P)
        
        if step <= ITERS_PHASE1:
            # Phase 1: Soft-min
            temp = 0.05 * (1.0 - step / ITERS_PHASE1) + 0.001
            # softmax weights for minimum (negative areas)
            weights = np.exp(-areas / temp)
            weights /= (np.sum(weights, axis=1, keepdims=True) + 1e-8)
        elif step <= ITERS_PHASE1 + ITERS_PHASE2:
            # Phase 2: Active-set subgradient
            min_areas = np.min(areas, axis=1, keepdims=True)
            active_mask = areas <= (min_areas + 1e-5)
            weights = active_mask.astype(float)
            weights /= (np.sum(weights, axis=1, keepdims=True) + 1e-8)
        else:
            # Phase 3: Fine-tuning with stricter active-set subgradient
            min_areas = np.min(areas, axis=1, keepdims=True)
            active_mask = areas <= (min_areas + 1e-7)
            weights = active_mask.astype(float)
            weights /= (np.sum(weights, axis=1, keepdims=True) + 1e-8)
            
        # We want to MAXIMIZE the area, so gradient is positive in direction of increasing area
        # cross is the signed area. The absolute area is abs(cross).
        # d(|x|)/dx = sign(x)
        signs = np.sign(cross)
        
        # For each combination, gradient with respect to p0, p1, p2
        # cross = (u1-u0)*(v2-v0) - (u2-u0)*(v1-v0)
        # d(cross)/du0 = -(v2-v0) + (v1-v0) = v1 - v2
        # d(cross)/dv0 = -(u1-u0) + (u2-u0) = u2 - u1
        # d(cross)/du1 = v2 - v0
        # d(cross)/dv1 = u0 - u2
        # d(cross)/du2 = v0 - v1
        # d(cross)/dv2 = u1 - u0
        
        p0 = P[:, idx0, :]
        p1 = P[:, idx1, :]
        p2 = P[:, idx2, :]
        
        dp0_u = p1[:, :, 1] - p2[:, :, 1]
        dp0_v = p2[:, :, 0] - p1[:, :, 0]
        dp1_u = p2[:, :, 1] - p0[:, :, 1]
        dp1_v = p0[:, :, 0] - p2[:, :, 0]
        dp2_u = p0[:, :, 1] - p1[:, :, 1]
        dp2_v = p1[:, :, 0] - p0[:, :, 0]
        
        # Multiply by sign and weight
        W = signs * weights  # (B, C)
        
        # Accumulate gradients
        # We have B batches, C combinations.
        # We can use np.add.at or a loop. Loop over C is 165, which is fast enough.
        grad_u = np.zeros((B, n))
        grad_v = np.zeros((B, n))
        
        for c in range(num_combs):
            w = W[:, c]
            i0, i1, i2 = idx0[c], idx1[c], idx2[c]
            
            grad_u[:, i0] += w * dp0_u[:, c]
            grad_v[:, i0] += w * dp0_v[:, c]
            
            grad_u[:, i1] += w * dp1_u[:, c]
            grad_v[:, i1] += w * dp1_v[:, c]
            
            grad_u[:, i2] += w * dp2_u[:, c]
            grad_v[:, i2] += w * dp2_v[:, c]
            
        grad = np.stack([grad_u, grad_v], axis=-1)
        
        # Adam update
        m = beta1 * m + (1 - beta1) * grad
        v_adam = beta2 * v_adam + (1 - beta2) * (grad ** 2)
        
        m_hat = m / (1 - beta1 ** step)
        v_hat = v_adam / (1 - beta2 ** step)
        
        P = P + lr * m_hat / (np.sqrt(v_hat) + epsilon)
        
        # Project
        P = project(P)
        
        # Decrease lr in phase 2 and phase 3
        if step == ITERS_PHASE1:
            lr *= 0.1
        elif step == ITERS_PHASE1 + ITERS_PHASE2:
            lr *= 0.1

    areas, _ = get_areas(P)
    min_areas = np.min(areas, axis=1)
    best_idx = np.argmax(min_areas)
    best_P = P[best_idx]
    best_min_area = min_areas[best_idx]
    
    # Convert back to (x, y)
    points = []
    for i in range(n):
        u, v = best_P[i]
        x = u + 0.5 * v
        y = math.sqrt(3.0) * 0.5 * v
        points.append([x, y])
        
    return np.array(points), best_min_area

def run_search_point(n=11):
    points, min_area = find_best_placement(n)
    return points.tolist(), float(min_area)
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))
```
