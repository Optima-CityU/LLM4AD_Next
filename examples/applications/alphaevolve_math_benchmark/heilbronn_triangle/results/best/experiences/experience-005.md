Hybrid pattern: lock topology with fast multi-start greedy search, then run exact SLSQP under fixed orientation constraints to maximize the true min-area.

- Hybrid Coordinate Descent and Exact SLSQP Maximization: This algorithm first uses greedy coordinate descent with decaying step sizes and multiple random restarts (200 restarts in the implementation) to lock in a strong triangle-orientation topology, then fixes determinant-sign orientation constraints and runs SLSQP to exactly maximize the minimum triangle area under containment, achieving target_ratio 0.959 and min_area 0.0350166 while outperforming Parent 1 (0.853) and Parent 2 (0.716).
- find_best_placement: Passing the Phase 1 topology to SLSQP mitigated the exact optimizer’s failures from poor random initializations noted in Parent 2 and enabled exact polishing that the discrete step-size method of Parent 1 could not attain; future designs facing combinatorial topology choices should reuse this pattern by seeding SLSQP with a strong greedy multi-start solution and enforcing fixed orientation constraints during the NLP.
- Coarse-to-Fine Hybrid Coordinate Descent with Exact SLSQP Polish: Used a two-stage filter with 2000 random initial configurations and a fast coarse coordinate descent to select the top 100 candidates, then applied fine coordinate descent with decaying step sizes followed by an SLSQP polish that fixes triangle orientation signs and enforces strict containment constraints.
- Coarse-to-Fine Hybrid Coordinate Descent with Exact SLSQP Polish: On the n=11 benchmark, this pipeline achieved min_area 0.0365298881927707 with validity 1.0, target_ratio 1.0008188545964576, and eval_time 710.365790012991 seconds, slightly surpassing the 0.0365 target.
- Coarse-to-Fine Hybrid Coordinate Descent with Exact SLSQP Polish: For future nonconvex geometric placement tasks with strict feasibility, reuse the pattern: wide coarse topology filtering to capture promising combinatorial structures, then fine coordinate descent and exact SLSQP polishing with orientation-sign constraints to maximize the min-area objective.
- Diverse Elite Expansion with Adaptive SQP Restarts: The search expanded SLSQP polishing from the best 8 annealed candidates to 12, retained the four candidates with the highest minimum determinant, and greedily filled the remaining slots using seeds farthest from the selected seeds in flattened barycentric-coordinate space.
- Diverse Elite Expansion with Adaptive SQP Restarts: When the second SLSQP pass failed to improve a refined candidate, the algorithm performed one adaptive retry using a very small zero-sum barycentric perturbation on points involved in the 14 smallest determinants, recomputed determinant signs, initialized the epigraph variable to 0.998 times the exact perturbed minimum determinant, and accepted the retry only if its exact minimum determinant improved.
- Diverse Elite Expansion with Adaptive SQP Restarts: The implementation achieved a target ratio of 1.0006988061575903 with min_area 0.036525506424752045, validity 1.0, and eval_time 877.026288296096 seconds.
- The Heilbronn triangle benchmark for n=11: The benchmark min_area for n=11 is 0.0365.
- Relaxed-Margin Continuation Polish keeps a conservative barycentric margin of 2e-5 during annealing, coordinate polishing, and the first SLSQP solve, then reruns the same signed-determinant SLSQP formulation with a 2e-7 strict-interior margin after each candidate has been refined.
- Relaxed-Margin Continuation Polish: The relaxed SLSQP pass starts from the already polished candidate, initializes its epigraph variable to approximately 0.995 times the candidate's exact minimum determinant, and retains the existing signed determinant constraints, analytic Jacobians, diversity-based elite selection, adaptive retry, and final exact validation.
- Relaxed-Margin Continuation Polish: A relaxed-pass result is accepted only when its recomputed minimum determinant is strictly larger and every barycentric coordinate remains above the final safety threshold; otherwise the existing candidate is retained.
- Relaxed-Margin Continuation Polish: For the n=11 Heilbronn triangle benchmark, the implementation achieved a score of 1.000698806157592 with min_area 0.03652550642475211, validity 1.0, evaluation time 924.3648694690783 seconds, and target_ratio 1.000698806157592.

```python
#!/usr/bin/env python3
"""Initial 11-point construction in an equilateral triangle."""

import json
import itertools
import math
import random

# EVOLVE_START
import numpy as np
from scipy.optimize import minimize

def run_search_point(n=11):
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")
        
    def find_best_placement(n):
        best_overall_points = None
        best_overall_area = -1

        # 200 random restarts to thoroughly explore the configuration space
        restarts = 200
        
        # Precompute indices for the pairs formed by the other 10 points
        idx1, idx2 = np.triu_indices(n - 1, k=1)
        
        # Generate base candidate offsets (64 angles, 3 radii scales)
        angles = np.linspace(0, 2*math.pi, 64, endpoint=False)
        base_radii = np.array([1.0, 0.5, 0.25])
        grid_r, grid_a = np.meshgrid(base_radii, angles)
        base_cand_dx = grid_r.flatten() * np.cos(grid_a.flatten())
        base_cand_dy = grid_r.flatten() * np.sin(grid_a.flatten())
        base_cand_offsets = np.column_stack((base_cand_dx, base_cand_dy))
        
        rng = np.random.RandomState(42)
        
        # Precompute triplets for Phase 2 (SLSQP)
        triplets = list(itertools.combinations(range(n), 3))
        num_triplets = len(triplets)
        triplets_array = np.array(triplets)
        idx_A = triplets_array[:, 0]
        idx_B = triplets_array[:, 1]
        idx_C = triplets_array[:, 2]
        row_idx = np.arange(num_triplets)
        
        sqrt3 = math.sqrt(3.0)
        
        # Precompute containment Jacobian and objective Jacobian for SLSQP
        # The variables are [x1, y1, x2, y2, ..., xn, yn, z] where z is the min area.
        containment_jac = np.zeros((3 * n, 2 * n + 1))
        for i in range(n):
            # Derivative of y >= margin wrt y_i
            containment_jac[i, 2 * i + 1] = 1.0
            # Derivative of sqrt(3)*x - y >= margin wrt x_i and y_i
            containment_jac[n + i, 2 * i] = sqrt3
            containment_jac[n + i, 2 * i + 1] = -1.0
            # Derivative of sqrt(3)*(1 - x) - y >= margin wrt x_i and y_i
            containment_jac[2 * n + i, 2 * i] = -sqrt3
            containment_jac[2 * n + i, 2 * i + 1] = -1.0
            
        obj_jac = np.zeros(2 * n + 1)
        obj_jac[-1] = -1.0
        
        margin = 1e-8
        bounds = [(0, 1), (0, sqrt3/2)] * n + [(0, 1)]
        
        for restart in range(restarts):
            # Phase 1: Random initialization strictly inside the equilateral triangle
            points = []
            for _ in range(n):
                u = rng.uniform(0, 1)
                v = rng.uniform(0, 1)
                if u + v > 1.0:
                    u, v = 1.0 - u, 1.0 - v
                # Ensure it starts well inside to respect the margin
                u = 0.01 + u * 0.98
                v = 0.01 + v * 0.98
                if u + v > 0.99:
                    u, v = 0.99 - v, 0.99 - u
                points.append([u + 0.5 * v, sqrt3 * 0.5 * v])
            points = np.array(points)
            
            step_size = 0.1
            while step_size > 1e-6:
                cand_offsets = base_cand_offsets * step_size
                
                improved = True
                iters = 0
                # Coordinate descent: optimize one point at a time
                while improved and iters < 50:
                    improved = False
                    iters += 1
                    for i in range(n):
                        others = np.delete(points, i, axis=0)
                        
                        A = others[idx1]
                        B = others[idx2]
                        
                        candidates = points[i] + cand_offsets
                        
                        # Strictly enforce barycentric interior constraints
                        v_cand = candidates[:, 1] / (sqrt3 / 2.0)
                        u_cand = candidates[:, 0] - 0.5 * v_cand
                        valid_mask = (u_cand >= 1e-8) & (v_cand >= 1e-8) & (u_cand + v_cand <= 1 - 1e-8)
                        valid_candidates = candidates[valid_mask]
                        
                        if len(valid_candidates) == 0:
                            continue
                            
                        # Vectorized area calculation for all valid candidates
                        dx = B[:,0] - A[:,0] 
                        dy = B[:,1] - A[:,1] 
                        
                        cand_x = valid_candidates[:, 0:1]
                        cand_y = valid_candidates[:, 1:2]
                        
                        A_x = A[:, 0]
                        A_y = A[:, 1]
                        
                        areas_incl = 0.5 * np.abs(
                            dx * (cand_y - A_y) - dy * (cand_x - A_x)
                        ) 
                        
                        # Minimum area of triangles involving the candidate point
                        min_areas_incl = np.min(areas_incl, axis=1) 
                        
                        # Minimum area of triangles involving the current point
                        curr_area_incl = 0.5 * np.abs(
                            dx * (points[i, 1] - A_y) - dy * (points[i, 0] - A_x)
                        )
                        curr_min_area_incl = np.min(curr_area_incl)
                        
                        # Strictly improve the true minimum area of triangles involving that point
                        best_cand_idx = np.argmax(min_areas_incl)
                        if min_areas_incl[best_cand_idx] > curr_min_area_incl + 1e-12:
                            points[i] = valid_candidates[best_cand_idx]
                            improved = True
                
                # Halve the step size for finer local search
                step_size *= 0.5
                
            # Phase 2: SLSQP exact polishing
            # Extract current configuration and topologies (signs of determinants)
            A_pts = points[idx_A]
            B_pts = points[idx_B]
            C_pts = points[idx_C]
            
            det = (B_pts[:, 0] - A_pts[:, 0]) * (C_pts[:, 1] - A_pts[:, 1]) - (B_pts[:, 1] - A_pts[:, 1]) * (C_pts[:, 0] - A_pts[:, 0])
            signs_array = np.sign(det)
            signs_array[signs_array == 0] = 1  # Handle collinearity safely
            
            min_a = np.min(0.5 * np.abs(det))
            
            x0 = np.zeros(2 * n + 1)
            x0[:-1] = points.flatten()
            x0[-1] = min_a
            
            # NLP Formulation: Maximize z (minimize -z) subject to area constraints and containment constraints
            def area_constraints(x):
                pts = x[:-1].reshape((n, 2))
                z = x[-1]
                A_pts = pts[idx_A]
                B_pts = pts[idx_B]
                C_pts = pts[idx_C]
                det_x = (B_pts[:, 0] - A_pts[:, 0]) * (C_pts[:, 1] - A_pts[:, 1]) - (B_pts[:, 1] - A_pts[:, 1]) * (C_pts[:, 0] - A_pts[:, 0])
                return 0.5 * signs_array * det_x - z
                
            def area_constraints_jac(x):
                pts = x[:-1].reshape((n, 2))
                jac = np.zeros((num_triplets, 2 * n + 1))
                A_pts = pts[idx_A]
                B_pts = pts[idx_B]
                C_pts = pts[idx_C]
                
                coef = 0.5 * signs_array
                
                dA_x = coef * (B_pts[:, 1] - C_pts[:, 1])
                dA_y = coef * (C_pts[:, 0] - B_pts[:, 0])
                dB_x = coef * (C_pts[:, 1] - A_pts[:, 1])
                dB_y = coef * (A_pts[:, 0] - C_pts[:, 0])
                dC_x = coef * (A_pts[:, 1] - B_pts[:, 1])
                dC_y = coef * (B_pts[:, 0] - A_pts[:, 0])
                
                jac[row_idx, 2 * idx_A] = dA_x
                jac[row_idx, 2 * idx_A + 1] = dA_y
                jac[row_idx, 2 * idx_B] = dB_x
                jac[row_idx, 2 * idx_B + 1] = dB_y
                jac[row_idx, 2 * idx_C] = dC_x
                jac[row_idx, 2 * idx_C + 1] = dC_y
                
                jac[:, -1] = -1.0
                return jac

            def containment_constraints(x):
                pts = x[:-1].reshape((n, 2))
                c1 = pts[:, 1] - margin
                c2 = sqrt3 * pts[:, 0] - pts[:, 1] - margin
                c3 = sqrt3 * (1.0 - pts[:, 0]) - pts[:, 1] - margin
                return np.concatenate([c1, c2, c3])
                
            cons = [
                {'type': 'ineq', 'fun': area_constraints, 'jac': area_constraints_jac},
                {'type': 'ineq', 'fun': containment_constraints, 'jac': lambda x: containment_jac}
            ]
            
            res = minimize(
                lambda x: -x[-1], 
                x0, 
                jac=lambda x: obj_jac,
                method='SLSQP', 
                bounds=bounds, 
                constraints=cons, 
                options={'maxiter': 200, 'ftol': 1e-9, 'disp': False}
            )
            
            if -res.fun > min_a:
                cand_points = res.x[:-1].reshape((n, 2))
                u_cand = cand_points[:, 0] - cand_points[:, 1] / sqrt3
                v_cand = cand_points[:, 1] / (sqrt3 / 2.0)
                w_cand = 1.0 - u_cand - v_cand
                
                # Check for strict containment mathematically
                if np.all(u_cand >= 1e-11) and np.all(v_cand >= 1e-11) and np.all(w_cand >= 1e-11):
                    A_cand = cand_points[idx_A]
                    B_cand = cand_points[idx_B]
                    C_cand = cand_points[idx_C]
                    det_cand = (B_cand[:, 0] - A_cand[:, 0]) * (C_cand[:, 1] - A_cand[:, 1]) - (B_cand[:, 1] - A_cand[:, 1]) * (C_cand[:, 0] - A_cand[:, 0])
                    true_min_a = np.min(0.5 * np.abs(det_cand))
                    
                    if true_min_a > min_a:
                        final_area = true_min_a
                        final_points = cand_points
                    else:
                        final_area = min_a
                        final_points = points
                else:
                    final_area = min_a
                    final_points = points
            else:
                final_area = min_a
                final_points = points
                
            if final_area > best_overall_area:
                best_overall_area = final_area
                best_overall_points = np.copy(final_points)
                
        # Return points as a 2D NumPy array and the normalized minimum area
        return best_overall_points, best_overall_area / (sqrt3 / 4.0)

    return find_best_placement(n)
# EVOLVE_END

if __name__ == "__main__":
    points, min_area = run_search_point(11)
    # Convert numpy array to list for JSON serialization in manual testing
    if hasattr(points, 'tolist'):
        points = points.tolist()
    print(json.dumps({"points": points, "min_area": min_area}))
```

```python
#!/usr/bin/env python3
"""Initial 11-point construction in an equilateral triangle."""

import json
import itertools
import math
import random

# EVOLVE_START
import numpy as np
from scipy.optimize import minimize

def run_search_point(n=11):
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")
        
    def find_best_placement(n):
        best_overall_points = None
        best_overall_area = -1

        # Phase 1: 2000 random restarts for coarse broad search
        coarse_restarts = 2000
        # Phase 2 & 3: Top 100 for fine deep search and exact polish
        fine_restarts = 100
        
        # Precompute indices for the pairs formed by the other 10 points
        idx1, idx2 = np.triu_indices(n - 1, k=1)
        
        # Generate coarse candidate offsets (32 angles, 2 radii scales)
        angles_coarse = np.linspace(0, 2*math.pi, 32, endpoint=False)
        base_radii_coarse = np.array([1.0, 0.5])
        grid_r_c, grid_a_c = np.meshgrid(base_radii_coarse, angles_coarse)
        base_cand_offsets_coarse = np.column_stack((
            grid_r_c.flatten() * np.cos(grid_a_c.flatten()),
            grid_r_c.flatten() * np.sin(grid_a_c.flatten())
        ))

        # Generate fine candidate offsets (64 angles, 3 radii scales)
        angles_fine = np.linspace(0, 2*math.pi, 64, endpoint=False)
        base_radii_fine = np.array([1.0, 0.5, 0.25])
        grid_r_f, grid_a_f = np.meshgrid(base_radii_fine, angles_fine)
        base_cand_offsets_fine = np.column_stack((
            grid_r_f.flatten() * np.cos(grid_a_f.flatten()),
            grid_r_f.flatten() * np.sin(grid_a_f.flatten())
        ))
        
        rng = np.random.RandomState(42)
        
        # Precompute triplets for Phase 3 (SLSQP)
        triplets = list(itertools.combinations(range(n), 3))
        num_triplets = len(triplets)
        triplets_array = np.array(triplets)
        idx_A = triplets_array[:, 0]
        idx_B = triplets_array[:, 1]
        idx_C = triplets_array[:, 2]
        row_idx = np.arange(num_triplets)
        
        sqrt3 = math.sqrt(3.0)
        
        # Precompute containment Jacobian and objective Jacobian for SLSQP
        # The variables are [x1, y1, x2, y2, ..., xn, yn, z] where z is the min area.
        containment_jac = np.zeros((3 * n, 2 * n + 1))
        for i in range(n):
            # Derivative of y >= margin wrt y_i
            containment_jac[i, 2 * i + 1] = 1.0
            # Derivative of sqrt(3)*x - y >= margin wrt x_i and y_i
            containment_jac[n + i, 2 * i] = sqrt3
            containment_jac[n + i, 2 * i + 1] = -1.0
            # Derivative of sqrt(3)*(1 - x) - y >= margin wrt x_i and y_i
            containment_jac[2 * n + i, 2 * i] = -sqrt3
            containment_jac[2 * n + i, 2 * i + 1] = -1.0
            
        obj_jac = np.zeros(2 * n + 1)
        obj_jac[-1] = -1.0
        
        margin = 1e-8
        bounds = [(0, 1), (0, sqrt3/2)] * n + [(0, 1)]
        
        coarse_results = []
        
        # 1. Coarse Broad Search
        for restart in range(coarse_restarts):
            # Random initialization strictly inside the equilateral triangle
            points = []
            for _ in range(n):
                u = rng.uniform(0, 1)
                v = rng.uniform(0, 1)
                if u + v > 1.0:
                    u, v = 1.0 - u, 1.0 - v
                # Ensure it starts well inside to respect the margin
                # This guarantees u >= 0.01, v >= 0.01, and u+v <= 0.99
                u = 0.01 + u * 0.97
                v = 0.01 + v * 0.97
                points.append([u + 0.5 * v, sqrt3 * 0.5 * v])
            points = np.array(points)
            
            step_size = 0.1
            # Early stopping at step_size 0.0125
            while step_size >= 0.0125 - 1e-9:
                cand_offsets = base_cand_offsets_coarse * step_size
                
                improved = True
                iters = 0
                # Coordinate descent: optimize one point at a time
                while improved and iters < 50:
                    improved = False
                    iters += 1
                    for i in range(n):
                        others = np.delete(points, i, axis=0)
                        
                        A = others[idx1]
                        B = others[idx2]
                        
                        candidates = points[i] + cand_offsets
                        
                        # Strictly enforce barycentric interior constraints
                        v_cand = candidates[:, 1] / (sqrt3 / 2.0)
                        u_cand = candidates[:, 0] - 0.5 * v_cand
                        valid_mask = (u_cand >= 1e-8) & (v_cand >= 1e-8) & (u_cand + v_cand <= 1 - 1e-8)
                        valid_candidates = candidates[valid_mask]
                        
                        if len(valid_candidates) == 0:
                            continue
                            
                        # Vectorized area calculation for all valid candidates
                        dx = B[:,0] - A[:,0] 
                        dy = B[:,1] - A[:,1] 
                        
                        cand_x = valid_candidates[:, 0:1]
                        cand_y = valid_candidates[:, 1:2]
                        
                        A_x = A[:, 0]
                        A_y = A[:, 1]
                        
                        areas_incl = 0.5 * np.abs(
                            dx * (cand_y - A_y) - dy * (cand_x - A_x)
                        ) 
                        
                        # Minimum area of triangles involving the candidate point
                        min_areas_incl = np.min(areas_incl, axis=1) 
                        
                        # Minimum area of triangles involving the current point
                        curr_area_incl = 0.5 * np.abs(
                            dx * (points[i, 1] - A_y) - dy * (points[i, 0] - A_x)
                        )
                        curr_min_area_incl = np.min(curr_area_incl)
                        
                        # Strictly improve the true minimum area of triangles involving that point
                        best_cand_idx = np.argmax(min_areas_incl)
                        if min_areas_incl[best_cand_idx] > curr_min_area_incl + 1e-12:
                            points[i] = valid_candidates[best_cand_idx]
                            improved = True
                
                # Halve the step size for finer local search
                step_size *= 0.5
                
            # Evaluate the coarse configuration
            A_pts = points[idx_A]
            B_pts = points[idx_B]
            C_pts = points[idx_C]
            det = (B_pts[:, 0] - A_pts[:, 0]) * (C_pts[:, 1] - A_pts[:, 1]) - (B_pts[:, 1] - A_pts[:, 1]) * (C_pts[:, 0] - A_pts[:, 0])
            min_a = np.min(0.5 * np.abs(det))
            coarse_results.append((min_a, points))

        # Select the top 100 configurations to proceed to the fine search
        coarse_results.sort(key=lambda x: x[0], reverse=True)
        top_configurations = coarse_results[:fine_restarts]
        
        # 2. Fine Deep Search & Exact Polish
        for _, init_points in top_configurations:
            points = np.copy(init_points)
            
            # Resume step_size from where coarse search ended
            step_size = 0.0125
            while step_size > 1e-6:
                cand_offsets = base_cand_offsets_fine * step_size
                
                improved = True
                iters = 0
                while improved and iters < 50:
                    improved = False
                    iters += 1
                    for i in range(n):
                        others = np.delete(points, i, axis=0)
                        
                        A = others[idx1]
                        B = others[idx2]
                        
                        candidates = points[i] + cand_offsets
                        
                        v_cand = candidates[:, 1] / (sqrt3 / 2.0)
                        u_cand = candidates[:, 0] - 0.5 * v_cand
                        valid_mask = (u_cand >= 1e-8) & (v_cand >= 1e-8) & (u_cand + v_cand <= 1 - 1e-8)
                        valid_candidates = candidates[valid_mask]
                        
                        if len(valid_candidates) == 0:
                            continue
                            
                        dx = B[:,0] - A[:,0] 
                        dy = B[:,1] - A[:,1] 
                        
                        cand_x = valid_candidates[:, 0:1]
                        cand_y = valid_candidates[:, 1:2]
                        
                        A_x = A[:, 0]
                        A_y = A[:, 1]
                        
                        areas_incl = 0.5 * np.abs(
                            dx * (cand_y - A_y) - dy * (cand_x - A_x)
                        ) 
                        
                        min_areas_incl = np.min(areas_incl, axis=1) 
                        
                        curr_area_incl = 0.5 * np.abs(
                            dx * (points[i, 1] - A_y) - dy * (points[i, 0] - A_x)
                        )
                        curr_min_area_incl = np.min(curr_area_incl)
                        
                        best_cand_idx = np.argmax(min_areas_incl)
                        if min_areas_incl[best_cand_idx] > curr_min_area_incl + 1e-12:
                            points[i] = valid_candidates[best_cand_idx]
                            improved = True
                
                step_size *= 0.5
                
            # 3. Exact polishing with SLSQP
            # Extract current configuration and topologies (signs of determinants)
            A_pts = points[idx_A]
            B_pts = points[idx_B]
            C_pts = points[idx_C]
            
            det = (B_pts[:, 0] - A_pts[:, 0]) * (C_pts[:, 1] - A_pts[:, 1]) - (B_pts[:, 1] - A_pts[:, 1]) * (C_pts[:, 0] - A_pts[:, 0])
            signs_array = np.sign(det)
            signs_array[signs_array == 0] = 1  # Handle collinearity safely
            
            min_a = np.min(0.5 * np.abs(det))
            
            x0 = np.zeros(2 * n + 1)
            x0[:-1] = points.flatten()
            x0[-1] = min_a
            
            # NLP Formulation: Maximize z (minimize -z) subject to area constraints and containment constraints
            def area_constraints(x):
                pts = x[:-1].reshape((n, 2))
                z = x[-1]
                A_pts = pts[idx_A]
                B_pts = pts[idx_B]
                C_pts = pts[idx_C]
                det_x = (B_pts[:, 0] - A_pts[:, 0]) * (C_pts[:, 1] - A_pts[:, 1]) - (B_pts[:, 1] - A_pts[:, 1]) * (C_pts[:, 0] - A_pts[:, 0])
                return 0.5 * signs_array * det_x - z
                
            def area_constraints_jac(x):
                pts = x[:-1].reshape((n, 2))
                jac = np.zeros((num_triplets, 2 * n + 1))
                A_pts = pts[idx_A]
                B_pts = pts[idx_B]
                C_pts = pts[idx_C]
                
                coef = 0.5 * signs_array
                
                dA_x = coef * (B_pts[:, 1] - C_pts[:, 1])
                dA_y = coef * (C_pts[:, 0] - B_pts[:, 0])
                dB_x = coef * (C_pts[:, 1] - A_pts[:, 1])
                dB_y = coef * (A_pts[:, 0] - C_pts[:, 0])
                dC_x = coef * (A_pts[:, 1] - B_pts[:, 1])
                dC_y = coef * (B_pts[:, 0] - A_pts[:, 0])
                
                jac[row_idx, 2 * idx_A] = dA_x
                jac[row_idx, 2 * idx_A + 1] = dA_y
                jac[row_idx, 2 * idx_B] = dB_x
                jac[row_idx, 2 * idx_B + 1] = dB_y
                jac[row_idx, 2 * idx_C] = dC_x
                jac[row_idx, 2 * idx_C + 1] = dC_y
                
                jac[:, -1] = -1.0
                return jac

            def containment_constraints(x):
                pts = x[:-1].reshape((n, 2))
                c1 = pts[:, 1] - margin
                c2 = sqrt3 * pts[:, 0] - pts[:, 1] - margin
                c3 = sqrt3 * (1.0 - pts[:, 0]) - pts[:, 1] - margin
                return np.concatenate([c1, c2, c3])
                
            cons = [
                {'type': 'ineq', 'fun': area_constraints, 'jac': area_constraints_jac},
                {'type': 'ineq', 'fun': containment_constraints, 'jac': lambda x: containment_jac}
            ]
            
            res = minimize(
                lambda x: -x[-1], 
                x0, 
                jac=lambda x: obj_jac,
                method='SLSQP', 
                bounds=bounds, 
                constraints=cons, 
                options={'maxiter': 200, 'ftol': 1e-9, 'disp': False}
            )
            
            if -res.fun > min_a:
                cand_points = res.x[:-1].reshape((n, 2))
                u_cand = cand_points[:, 0] - cand_points[:, 1] / sqrt3
                v_cand = cand_points[:, 1] / (sqrt3 / 2.0)
                w_cand = 1.0 - u_cand - v_cand
                
                # Check for strict containment mathematically
                if np.all(u_cand >= 1e-11) and np.all(v_cand >= 1e-11) and np.all(w_cand >= 1e-11):
                    A_cand = cand_points[idx_A]
                    B_cand = cand_points[idx_B]
                    C_cand = cand_points[idx_C]
                    det_cand = (B_cand[:, 0] - A_cand[:, 0]) * (C_cand[:, 1] - A_cand[:, 1]) - (B_cand[:, 1] - A_cand[:, 1]) * (C_cand[:, 0] - A_cand[:, 0])
                    true_min_a = np.min(0.5 * np.abs(det_cand))
                    
                    if true_min_a > min_a:
                        final_area = true_min_a
                        final_points = cand_points
                    else:
                        final_area = min_a
                        final_points = points
                else:
                    final_area = min_a
                    final_points = points
            else:
                final_area = min_a
                final_points = points
                
            if final_area > best_overall_area:
                best_overall_area = final_area
                best_overall_points = np.copy(final_points)
                
        # Return points as a 2D NumPy array and the normalized minimum area
        return best_overall_points, best_overall_area / (sqrt3 / 4.0)

    return find_best_placement(n)
# EVOLVE_END

if __name__ == "__main__":
    points, min_area = run_search_point(11)
    # Convert numpy array to list for JSON serialization in manual testing
    if hasattr(points, 'tolist'):
        points = points.tolist()
    print(json.dumps({"points": points, "min_area": min_area}))
```

```python
#!/usr/bin/env python3
"""Initial 11-point construction in an equilateral triangle."""

import json
import itertools
import math
import random


# EVOLVE_START
def run_search_point(n=11):
    """Search for a high-quality 11-point Heilbronn configuration."""
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")

    rng = random.Random(271828)
    eps = 2.0e-5
    triples = list(itertools.combinations(range(n), 3))

    def normalize(row):
        s = sum(row)
        return [max(eps, v / s) for v in row]

    def determinants(rows):
        result = []
        for i, j, k in triples:
            a, b, c = rows[i], rows[j], rows[k]
            result.append(
                a[0] * (b[1] * c[2] - b[2] * c[1])
                - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0])
            )
        return result

    def quality(rows):
        values = sorted(abs(x) for x in determinants(rows))
        count = min(18, len(values))
        return values[0], sum(values[:count]) / count

    def better(a, b):
        return a[0] > b[0] or (a[0] == b[0] and a[1] > b[1])

    def random_rows(concentration):
        rows = []
        for _ in range(n):
            values = [
                -math.log(max(1.0e-14, rng.random())) / concentration
                for _ in range(3)
            ]
            total = sum(values)
            rows.append([v / total for v in values])
        return rows

    def lattice_rows():
        level = 7
        pool = []
        for a in range(1, level):
            for b in range(1, level - a):
                c = level - a - b
                pool.append([a / level, b / level, c / level])
        rng.shuffle(pool)
        rows = [pool[i][:] for i in range(n)]
        for row in rows:
            noise = [rng.uniform(-0.035, 0.035) for _ in range(3)]
            mean = sum(noise) / 3.0
            row[:] = normalize(
                [row[q] + noise[q] - mean for q in range(3)]
            )
        return rows

    def folded_sequence():
        rows = []
        for index in range(1, n + 1):
            values = []
            for base in (2, 3, 5):
                value = 0.0
                factor = 1.0 / base
                m = index
                while m:
                    value += (m % base) * factor
                    m //= base
                    factor /= base
                values.append(0.08 + 0.84 * (2.0 * abs(value - 0.5)))
            rows.append(normalize(values))
        return rows

    def polish(rows, current, steps=None):
        """Coordinate-transfer active-set polish."""
        rows = [r[:] for r in rows]
        score = current
        if steps is None:
            steps = (0.006, 0.002, 0.0007)

        for step in steps:
            while True:
                ds = determinants(rows)
                active = sorted(
                    range(len(ds)), key=lambda z: abs(ds[z])
                )[:min(14, len(ds))]
                involved = set()
                for z in active:
                    involved.update(triples[z])

                changed = False
                for i in sorted(involved):
                    for p, q in (
                        (0, 1), (0, 2), (1, 0),
                        (1, 2), (2, 0), (2, 1),
                    ):
                        amount = min(step, rows[i][p] - eps)
                        if amount <= 0.0:
                            continue
                        trial = [r[:] for r in rows]
                        trial[i][p] -= amount
                        trial[i][q] += amount
                        if min(trial[i]) <= eps:
                            continue
                        trial_score = quality(trial)
                        if better(trial_score, score):
                            rows, score = trial, trial_score
                            changed = True
                            break
                    if changed:
                        break
                if not changed:
                    break
        return rows, score

    def anneal(start, iterations=13500):
        rows = [r[:] for r in start]
        best = [r[:] for r in rows]
        current = quality(rows)
        best_quality = current
        stagnant = 0
        last_polish = -1000

        for iteration in range(iterations):
            ds = determinants(rows)
            active = sorted(
                range(len(ds)), key=lambda z: abs(ds[z])
            )[:min(14, len(ds))]

            if rng.random() < 0.88:
                i = rng.choice(triples[rng.choice(active)])
            else:
                i = rng.randrange(n)

            p, q = rng.sample(range(3), 2)
            old = rows[i][:]
            fraction = iteration / float(max(1, iterations))
            remaining = 1.0 - fraction
            scale = 0.115 * remaining ** 1.45 + 0.0015
            delta = rng.uniform(-scale, scale)

            if delta >= 0:
                limit = min(rows[i][q] - eps, 1.0 - eps - rows[i][p])
            else:
                limit = min(rows[i][p] - eps, 1.0 - eps - rows[i][q])
            if limit <= 0:
                continue
            delta = max(-limit, min(limit, delta))

            rows[i][p] += delta
            rows[i][q] -= delta
            if min(rows[i]) <= eps:
                rows[i] = old
                continue

            new_quality = quality(rows)
            old_key = current[0] + 0.035 * current[1]
            new_key = new_quality[0] + 0.035 * new_quality[1]
            temperature = 0.0017 * remaining + 1.0e-7
            accept = new_key >= old_key
            if not accept:
                accept = rng.random() < math.exp(
                    (new_key - old_key) / temperature
                )

            improved = False
            if accept:
                current = new_quality
                if better(new_quality, best_quality):
                    best = [r[:] for r in rows]
                    best_quality = new_quality
                    stagnant = 0
                    improved = True
                else:
                    stagnant += 1
            else:
                rows[i] = old
                stagnant += 1

            if iteration % 250 == 0 or (
                improved and iteration - last_polish >= 40
            ):
                rows, current = polish(rows, current)
                last_polish = iteration
                if better(current, best_quality):
                    best = [r[:] for r in rows]
                    best_quality = current
                    stagnant = 0

            if stagnant >= 1450:
                rows = [r[:] for r in best]
                ds = determinants(rows)
                active = sorted(
                    range(len(ds)), key=lambda z: abs(ds[z])
                )[:min(14, len(ds))]
                counts = [0] * n
                for z in active:
                    for vertex in triples[z]:
                        counts[vertex] += 1
                focus = max(range(n), key=lambda z: counts[z])

                for _ in range(6):
                    p, q = rng.sample(range(3), 2)
                    old_value = rows[focus][:]
                    amount = rng.uniform(-0.028, 0.028)
                    if amount >= 0:
                        limit = min(
                            rows[focus][q] - eps,
                            1.0 - eps - rows[focus][p],
                        )
                    else:
                        limit = min(
                            rows[focus][p] - eps,
                            1.0 - eps - rows[focus][q],
                        )
                    if limit <= 0:
                        continue
                    amount = max(-limit, min(limit, amount))
                    rows[focus][p] += amount
                    rows[focus][q] -= amount
                    if min(rows[focus]) <= eps:
                        rows[focus] = old_value
                current = quality(rows)
                stagnant = 0

        polished_rows, polished_quality = polish(best, best_quality)
        return polished_rows if better(polished_quality, best_quality) else best

    starts = [lattice_rows(), folded_sequence()]
    for concentration in (0.45, 0.75, 1.0, 1.7, 3.0):
        starts.append(random_rows(concentration))
    while len(starts) < 16:
        starts.append(
            lattice_rows()
            if len(starts) % 3 == 0
            else random_rows(0.55 + 2.5 * rng.random())
        )

    candidates = []
    for start in starts:
        candidate = anneal(start)
        candidates.append((quality(candidate), candidate))

    candidates.sort(
        key=lambda item: (item[0][0], item[0][1]), reverse=True
    )

    try:
        import numpy as np
        from scipy.optimize import minimize

        def pack(rows, t):
            return np.array(
                [v for row in rows for v in row[:2]] + [t],
                dtype=float,
            )

        def unpack(vector):
            return [
                [
                    float(vector[2 * i]),
                    float(vector[2 * i + 1]),
                    1.0 - float(vector[2 * i]) - float(vector[2 * i + 1]),
                ]
                for i in range(n)
            ]

        def make_solver(seed):
            ds = determinants(seed)
            signs = [1.0 if d >= 0.0 else -1.0 for d in ds]

            def objective(vector):
                return -vector[-1]

            def objective_jac(vector):
                gradient = np.zeros(2 * n + 1)
                gradient[-1] = -1.0
                return gradient

            def constraint_values(vector):
                rows = unpack(vector)
                values = determinants(rows)
                result = [
                    signs[z] * values[z] - vector[-1]
                    for z in range(len(values))
                ]
                result.extend(row[2] - eps for row in rows)
                return np.asarray(result)

            def constraint_jac(vector):
                rows = unpack(vector)
                matrix = np.zeros((len(triples) + n, 2 * n + 1))
                for z, (i, j, k) in enumerate(triples):
                    ri, rj, rk = rows[i], rows[j], rows[k]
                    for who, u, v in (
                        (i, rj, rk), (j, rk, ri), (k, ri, rj)
                    ):
                        cross0 = u[1] * v[2] - u[2] * v[1]
                        cross1 = u[2] * v[0] - u[0] * v[2]
                        cross2 = u[0] * v[1] - u[1] * v[0]
                        matrix[z, 2 * who] = signs[z] * (cross0 - cross2)
                        matrix[z, 2 * who + 1] = signs[z] * (cross1 - cross2)
                    matrix[z, -1] = -1.0
                for i in range(n):
                    matrix[len(triples) + i, 2 * i] = -1.0
                    matrix[len(triples) + i, 2 * i + 1] = -1.0
                return matrix

            return minimize(
                objective,
                pack(seed, max(0.0, min(abs(d) for d in ds) * 0.92)),
                jac=objective_jac,
                constraints={
                    "type": "ineq",
                    "fun": constraint_values,
                    "jac": constraint_jac,
                },
                bounds=[(eps, 1.0 - eps)] * (2 * n) + [(0.0, 0.2)],
                method="SLSQP",
                options={"maxiter": 900, "ftol": 2.0e-11, "disp": False},
            )

        # Keep four best seeds, then use barycentric diversity for the rest.
        ranked = sorted(
            candidates,
            key=lambda item: (item[0][0], item[0][1]),
            reverse=True,
        )
        selected = ranked[:min(4, len(ranked))]
        remaining = ranked[min(4, len(ranked)):]
        while remaining and len(selected) < min(12, len(ranked)):
            def distance(item):
                vector = [v for row in item[1] for v in row]
                return min(
                    sum((a - b) ** 2 for a, b in zip(
                        vector, [v for row in other[1] for v in row]
                    ))
                    for other in selected
                )
            chosen = max(remaining, key=distance)
            selected.append(chosen)
            remaining.remove(chosen)

        polished = []
        for _, seed in selected:
            first = make_solver(seed)
            if not (first.success or first.x[-1] > 0):
                continue

            raw = unpack(first.x)
            raw_score = quality(raw)
            refined, refined_score = polish(
                raw, raw_score, steps=(0.002, 0.0007, 0.00022, 0.00007)
            )
            if better(refined_score, raw_score):
                raw, raw_score = refined, refined_score

            second = make_solver(raw)
            second_rows = unpack(second.x)
            second_score = quality(second_rows)

            if better(second_score, raw_score):
                polished.append(second_rows)
                continue

            # Controlled escape from a stalled local SLSQP solution.
            perturbed = [row[:] for row in raw]
            active = sorted(
                range(len(triples)),
                key=lambda z: abs(determinants(raw)[z])
            )[:min(14, len(triples))]
            involved = set()
            for z in active:
                involved.update(triples[z])
            for i in involved:
                shift = rng.uniform(-2.0e-5, 2.0e-5)
                perturbed[i][0] += shift
                perturbed[i][1] -= shift
                if min(perturbed[i]) <= eps:
                    perturbed[i] = raw[i][:]
            retry = make_solver(perturbed)
            retry_rows = unpack(retry.x)
            retry_score = quality(retry_rows)
            if better(retry_score, raw_score):
                polished.append(retry_rows)
            else:
                polished.append(raw)

        candidates.extend((quality(row), row) for row in polished)
    except Exception:
        pass

    candidates.sort(
        key=lambda item: (item[0][0], item[0][1]), reverse=True
    )
    rows = candidates[0][1]

    for step in (0.002, 0.0007, 0.00022, 0.00007):
        changed = True
        while changed:
            changed = False
            before = quality(rows)
            for i in range(n):
                for p, q in ((0, 1), (0, 2), (1, 2)):
                    for sign in (-1.0, 1.0):
                        if rows[i][q] - sign * step <= eps:
                            continue
                        trial = [r[:] for r in rows]
                        trial[i][p] += sign * step
                        trial[i][q] -= sign * step
                        if min(trial[i]) <= eps:
                            continue
                        after = quality(trial)
                        if better(after, before):
                            rows, before, changed = trial, after, True

    rows = [normalize(row) for row in rows]
    points = [
        [
            row[1] + 0.5 * row[2],
            math.sqrt(3.0) / 2.0 * row[2],
        ]
        for row in rows
    ]
    min_area = min(
        abs(
            (points[j][0] - points[i][0]) * (points[k][1] - points[i][1])
            - (points[j][1] - points[i][1]) * (points[k][0] - points[i][0])
        ) * 2.0 / math.sqrt(3.0)
        for i, j, k in triples
    )
    return points, float(min_area)
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))

```

```python
#!/usr/bin/env python3
"""Initial 11-point construction in an equilateral triangle."""

import json
import itertools
import math
import random


# EVOLVE_START
def run_search_point(n=11):
    """Search for a high-quality 11-point Heilbronn configuration."""
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")

    rng = random.Random(271828)
    eps = 2.0e-5
    final_margin = 2.0e-7
    triples = list(itertools.combinations(range(n), 3))

    def normalize(row, margin=eps):
        values = [max(margin, float(v)) for v in row]
        total = sum(values)
        return [v / total for v in values]

    def determinants(rows):
        result = []
        for i, j, k in triples:
            a, b, c = rows[i], rows[j], rows[k]
            result.append(
                a[0] * (b[1] * c[2] - b[2] * c[1])
                - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0])
            )
        return result

    def quality(rows):
        values = sorted(abs(v) for v in determinants(rows))
        count = min(18, len(values))
        return values[0], sum(values[:count]) / count

    def better(a, b):
        return a[0] > b[0] or (a[0] == b[0] and a[1] > b[1])

    def random_rows(concentration):
        result = []
        for _ in range(n):
            values = [
                -math.log(max(1.0e-14, rng.random())) / concentration
                for _ in range(3)
            ]
            result.append(normalize(values))
        return result

    def lattice_rows():
        level = 7
        pool = []
        for a in range(1, level):
            for b in range(1, level - a):
                pool.append([a / level, b / level, (level - a - b) / level])
        rng.shuffle(pool)
        result = []
        for row in pool[:n]:
            noise = [rng.uniform(-0.035, 0.035) for _ in range(3)]
            mean = sum(noise) / 3.0
            result.append(normalize([row[i] + noise[i] - mean for i in range(3)]))
        return result

    def folded_sequence():
        result = []
        for index in range(1, n + 1):
            values = []
            for base in (2, 3, 5):
                value = 0.0
                factor = 1.0 / base
                m = index
                while m:
                    value += (m % base) * factor
                    m //= base
                    factor /= base
                values.append(0.08 + 0.84 * (2.0 * abs(value - 0.5)))
            result.append(normalize(values))
        return result

    def polish(rows, current, steps=None, margin=eps):
        rows = [r[:] for r in rows]
        if steps is None:
            steps = (0.006, 0.002, 0.0007)
        score = current

        for step in steps:
            while True:
                ds = determinants(rows)
                active = sorted(range(len(ds)), key=lambda z: abs(ds[z]))[:14]
                involved = set()
                for z in active:
                    involved.update(triples[z])

                changed = False
                for i in sorted(involved):
                    for p, q in (
                        (0, 1), (0, 2), (1, 0),
                        (1, 2), (2, 0), (2, 1),
                    ):
                        amount = min(step, rows[i][p] - margin)
                        if amount <= 0.0:
                            continue
                        trial = [r[:] for r in rows]
                        trial[i][p] -= amount
                        trial[i][q] += amount
                        if min(trial[i]) <= margin:
                            continue
                        trial_score = quality(trial)
                        if better(trial_score, score):
                            rows, score = trial, trial_score
                            changed = True
                            break
                    if changed:
                        break
                if not changed:
                    break
        return rows, score

    def anneal(start, iterations=13500):
        rows = [r[:] for r in start]
        best = [r[:] for r in rows]
        current = quality(rows)
        best_quality = current
        stagnant = 0
        last_polish = -1000

        for iteration in range(iterations):
            ds = determinants(rows)
            active = sorted(range(len(ds)), key=lambda z: abs(ds[z]))[:14]
            if rng.random() < 0.88:
                i = rng.choice(triples[rng.choice(active)])
            else:
                i = rng.randrange(n)

            p, q = rng.sample(range(3), 2)
            old = rows[i][:]
            fraction = iteration / float(max(1, iterations))
            remaining = 1.0 - fraction
            scale = 0.115 * remaining ** 1.45 + 0.0015
            delta = rng.uniform(-scale, scale)

            if delta >= 0.0:
                limit = min(rows[i][q] - eps, 1.0 - eps - rows[i][p])
            else:
                limit = min(rows[i][p] - eps, 1.0 - eps - rows[i][q])
            if limit <= 0.0:
                continue
            delta = max(-limit, min(limit, delta))
            rows[i][p] += delta
            rows[i][q] -= delta

            if min(rows[i]) <= eps:
                rows[i] = old
                continue

            new_quality = quality(rows)
            old_key = current[0] + 0.035 * current[1]
            new_key = new_quality[0] + 0.035 * new_quality[1]
            temperature = 0.0017 * remaining + 1.0e-7
            accept = new_key >= old_key
            if not accept:
                accept = rng.random() < math.exp(
                    (new_key - old_key) / temperature
                )

            improved = False
            if accept:
                current = new_quality
                if better(new_quality, best_quality):
                    best = [r[:] for r in rows]
                    best_quality = new_quality
                    stagnant = 0
                    improved = True
                else:
                    stagnant += 1
            else:
                rows[i] = old
                stagnant += 1

            if iteration % 250 == 0 or (
                improved and iteration - last_polish >= 40
            ):
                rows, current = polish(rows, current)
                last_polish = iteration
                if better(current, best_quality):
                    best = [r[:] for r in rows]
                    best_quality = current
                    stagnant = 0

            if stagnant >= 1450:
                rows = [r[:] for r in best]
                ds = determinants(rows)
                active = sorted(range(len(ds)), key=lambda z: abs(ds[z]))[:14]
                counts = [0] * n
                for z in active:
                    for vertex in triples[z]:
                        counts[vertex] += 1
                focus = max(range(n), key=lambda z: counts[z])

                for _ in range(6):
                    p, q = rng.sample(range(3), 2)
                    old_value = rows[focus][:]
                    amount = rng.uniform(-0.028, 0.028)
                    if amount >= 0.0:
                        limit = min(
                            rows[focus][q] - eps,
                            1.0 - eps - rows[focus][p],
                        )
                    else:
                        limit = min(
                            rows[focus][p] - eps,
                            1.0 - eps - rows[focus][q],
                        )
                    if limit <= 0.0:
                        continue
                    amount = max(-limit, min(limit, amount))
                    rows[focus][p] += amount
                    rows[focus][q] -= amount
                    if min(rows[focus]) <= eps:
                        rows[focus] = old_value
                current = quality(rows)
                stagnant = 0

        polished_rows, polished_quality = polish(best, best_quality)
        return polished_rows if better(polished_quality, best_quality) else best

    starts = [lattice_rows(), folded_sequence()]
    for concentration in (0.45, 0.75, 1.0, 1.7, 3.0):
        starts.append(random_rows(concentration))
    while len(starts) < 16:
        starts.append(
            lattice_rows()
            if len(starts) % 3 == 0
            else random_rows(0.55 + 2.5 * rng.random())
        )

    candidates = []
    for start in starts:
        candidate = anneal(start)
        candidates.append((quality(candidate), candidate))
    candidates.sort(key=lambda item: item[0], reverse=True)

    try:
        import numpy as np
        from scipy.optimize import minimize

        def pack(rows, t):
            return np.array(
                [v for row in rows for v in row[:2]] + [t],
                dtype=float,
            )

        def unpack(vector):
            return [
                [
                    float(vector[2 * i]),
                    float(vector[2 * i + 1]),
                    1.0 - float(vector[2 * i]) - float(vector[2 * i + 1]),
                ]
                for i in range(n)
            ]

        def make_solver(seed, margin=eps, initial_t=None):
            ds = determinants(seed)
            signs = [1.0 if d >= 0.0 else -1.0 for d in ds]
            if initial_t is None:
                initial_t = max(0.0, min(abs(d) for d in ds) * 0.92)

            def objective(vector):
                return -vector[-1]

            def objective_jac(vector):
                gradient = np.zeros(2 * n + 1)
                gradient[-1] = -1.0
                return gradient

            def constraint_values(vector):
                rows = unpack(vector)
                values = determinants(rows)
                result = [
                    signs[z] * values[z] - vector[-1]
                    for z in range(len(values))
                ]
                result.extend(row[2] - margin for row in rows)
                return np.asarray(result)

            def constraint_jac(vector):
                rows = unpack(vector)
                matrix = np.zeros((len(triples) + n, 2 * n + 1))
                for z, (i, j, k) in enumerate(triples):
                    ri, rj, rk = rows[i], rows[j], rows[k]
                    for who, u, v in (
                        (i, rj, rk), (j, rk, ri), (k, ri, rj)
                    ):
                        cross0 = u[1] * v[2] - u[2] * v[1]
                        cross1 = u[2] * v[0] - u[0] * v[2]
                        cross2 = u[0] * v[1] - u[1] * v[0]
                        matrix[z, 2 * who] = signs[z] * (cross0 - cross2)
                        matrix[z, 2 * who + 1] = signs[z] * (cross1 - cross2)
                    matrix[z, -1] = -1.0

                for i in range(n):
                    matrix[len(triples) + i, 2 * i] = -1.0
                    matrix[len(triples) + i, 2 * i + 1] = -1.0
                return matrix

            return minimize(
                objective,
                pack(seed, initial_t),
                jac=objective_jac,
                constraints={
                    "type": "ineq",
                    "fun": constraint_values,
                    "jac": constraint_jac,
                },
                bounds=[(margin, 1.0 - margin)] * (2 * n) + [(0.0, 0.2)],
                method="SLSQP",
                options={
                    "maxiter": 900,
                    "ftol": 2.0e-11,
                    "disp": False,
                },
            )

        ranked = sorted(
            candidates,
            key=lambda item: (item[0][0], item[0][1]),
            reverse=True,
        )
        selected = ranked[:min(4, len(ranked))]
        remaining = ranked[min(4, len(ranked)):]

        while remaining and len(selected) < min(12, len(ranked)):
            def distance(item):
                vector = [v for row in item[1] for v in row]
                return min(
                    sum(
                        (a - b) ** 2
                        for a, b in zip(
                            vector,
                            [v for row in other[1] for v in row],
                        )
                    )
                    for other in selected
                )

            chosen = max(remaining, key=distance)
            selected.append(chosen)
            remaining.remove(chosen)

        polished = []
        for _, seed in selected:
            first = make_solver(seed)
            if not (first.success or first.x[-1] > 0.0):
                continue

            raw = unpack(first.x)
            raw_score = quality(raw)
            refined, refined_score = polish(
                raw,
                raw_score,
                steps=(0.002, 0.0007, 0.00022, 0.00007),
            )
            if better(refined_score, raw_score):
                raw, raw_score = refined, refined_score

            second = make_solver(raw)
            if second.success or second.x[-1] > 0.0:
                second_rows = unpack(second.x)
                second_score = quality(second_rows)
            else:
                second_rows, second_score = raw, raw_score

            if better(second_score, raw_score):
                raw, raw_score = second_rows, second_score

            # Continuation pass: retain the topology and all analytic
            # determinant constraints, but permit points to approach the
            # boundary more closely than the conservative first solve.
            relaxed_start_t = max(
                0.0, 0.995 * min(abs(d) for d in determinants(raw))
            )
            relaxed = make_solver(
                raw,
                margin=final_margin,
                initial_t=relaxed_start_t,
            )
            if relaxed.success or relaxed.x[-1] > 0.0:
                relaxed_rows = unpack(relaxed.x)
                relaxed_score = quality(relaxed_rows)
                valid = all(
                    min(row) > final_margin for row in relaxed_rows
                )
                if valid and relaxed_score[0] > raw_score[0]:
                    raw, raw_score = relaxed_rows, relaxed_score

            # A small topology-locked retry is useful when SLSQP terminates
            # at a stationary point without improving the epigraph.
            perturbed = [row[:] for row in raw]
            active = sorted(
                range(len(triples)),
                key=lambda z: abs(determinants(raw)[z]),
            )[:14]
            involved = set()
            for z in active:
                involved.update(triples[z])
            for i in involved:
                shift = rng.uniform(-2.0e-5, 2.0e-5)
                perturbed[i][0] += shift
                perturbed[i][1] -= shift
                if min(perturbed[i]) <= eps:
                    perturbed[i] = raw[i][:]

            retry = make_solver(perturbed)
            if retry.success or retry.x[-1] > 0.0:
                retry_rows = unpack(retry.x)
                retry_score = quality(retry_rows)
                if better(retry_score, raw_score):
                    raw, raw_score = retry_rows, retry_score

            polished.append(raw)

        candidates.extend((quality(row), row) for row in polished)
    except Exception:
        pass

    candidates.sort(key=lambda item: item[0], reverse=True)
    rows = candidates[0][1]

    for step in (0.002, 0.0007, 0.00022, 0.00007):
        changed = True
        while changed:
            changed = False
            before = quality(rows)
            for i in range(n):
                for p, q in ((0, 1), (0, 2), (1, 2)):
                    for sign in (-1.0, 1.0):
                        if rows[i][q] - sign * step <= final_margin:
                            continue
                        trial = [r[:] for r in rows]
                        trial[i][p] += sign * step
                        trial[i][q] -= sign * step
                        if min(trial[i]) <= final_margin:
                            continue
                        after = quality(trial)
                        if better(after, before):
                            rows, before, changed = trial, after, True

    rows = [normalize(row, final_margin) for row in rows]
    points = [
        [
            row[1] + 0.5 * row[2],
            math.sqrt(3.0) / 2.0 * row[2],
        ]
        for row in rows
    ]
    min_area = min(
        abs(
            (points[j][0] - points[i][0]) * (points[k][1] - points[i][1])
            - (points[j][1] - points[i][1]) * (points[k][0] - points[i][0])
        ) * 2.0 / math.sqrt(3.0)
        for i, j, k in triples
    )
    return points, float(min_area)
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))

```

```python
#!/usr/bin/env python3
"""Initial 11-point construction in an equilateral triangle."""

import json
import itertools
import math
import random

# EVOLVE_START
import numpy as np
from scipy.optimize import minimize

def run_search_point(n=11):
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")
        
    def find_best_placement(n):
        best_overall_points = None
        best_overall_area = -1

        # 200 random restarts to thoroughly explore the configuration space
        restarts = 200
        
        # Precompute indices for the pairs formed by the other 10 points
        idx1, idx2 = np.triu_indices(n - 1, k=1)
        
        # Generate base candidate offsets (64 angles, 3 radii scales)
        angles = np.linspace(0, 2*math.pi, 64, endpoint=False)
        base_radii = np.array([1.0, 0.5, 0.25])
        grid_r, grid_a = np.meshgrid(base_radii, angles)
        base_cand_dx = grid_r.flatten() * np.cos(grid_a.flatten())
        base_cand_dy = grid_r.flatten() * np.sin(grid_a.flatten())
        base_cand_offsets = np.column_stack((base_cand_dx, base_cand_dy))
        
        rng = np.random.RandomState(42)
        
        # Precompute triplets for Phase 2 (SLSQP)
        triplets = list(itertools.combinations(range(n), 3))
        num_triplets = len(triplets)
        triplets_array = np.array(triplets)
        idx_A = triplets_array[:, 0]
        idx_B = triplets_array[:, 1]
        idx_C = triplets_array[:, 2]
        row_idx = np.arange(num_triplets)
        
        sqrt3 = math.sqrt(3.0)
        
        # Precompute containment Jacobian and objective Jacobian for SLSQP
        # The variables are [x1, y1, x2, y2, ..., xn, yn, z] where z is the min area.
        containment_jac = np.zeros((3 * n, 2 * n + 1))
        for i in range(n):
            # Derivative of y >= margin wrt y_i
            containment_jac[i, 2 * i + 1] = 1.0
            # Derivative of sqrt(3)*x - y >= margin wrt x_i and y_i
            containment_jac[n + i, 2 * i] = sqrt3
            containment_jac[n + i, 2 * i + 1] = -1.0
            # Derivative of sqrt(3)*(1 - x) - y >= margin wrt x_i and y_i
            containment_jac[2 * n + i, 2 * i] = -sqrt3
            containment_jac[2 * n + i, 2 * i + 1] = -1.0
            
        obj_jac = np.zeros(2 * n + 1)
        obj_jac[-1] = -1.0
        
        margin = 1e-8
        bounds = [(0, 1), (0, sqrt3/2)] * n + [(0, 1)]
        
        for restart in range(restarts):
            # Phase 1: Random initialization strictly inside the equilateral triangle
            points = []
            for _ in range(n):
                u = rng.uniform(0, 1)
                v = rng.uniform(0, 1)
                if u + v > 1.0:
                    u, v = 1.0 - u, 1.0 - v
                # Ensure it starts well inside to respect the margin
                u = 0.01 + u * 0.98
                v = 0.01 + v * 0.98
                if u + v > 0.99:
                    u, v = 0.99 - v, 0.99 - u
                points.append([u + 0.5 * v, sqrt3 * 0.5 * v])
            points = np.array(points)
            
            step_size = 0.1
            while step_size > 1e-6:
                cand_offsets = base_cand_offsets * step_size
                
                improved = True
                iters = 0
                # Coordinate descent: optimize one point at a time
                while improved and iters < 50:
                    improved = False
                    iters += 1
                    for i in range(n):
                        others = np.delete(points, i, axis=0)
                        
                        A = others[idx1]
                        B = others[idx2]
                        
                        candidates = points[i] + cand_offsets
                        
                        # Strictly enforce barycentric interior constraints
                        v_cand = candidates[:, 1] / (sqrt3 / 2.0)
                        u_cand = candidates[:, 0] - 0.5 * v_cand
                        valid_mask = (u_cand >= 1e-8) & (v_cand >= 1e-8) & (u_cand + v_cand <= 1 - 1e-8)
                        valid_candidates = candidates[valid_mask]
                        
                        if len(valid_candidates) == 0:
                            continue
                            
                        # Vectorized area calculation for all valid candidates
                        dx = B[:,0] - A[:,0] 
                        dy = B[:,1] - A[:,1] 
                        
                        cand_x = valid_candidates[:, 0:1]
                        cand_y = valid_candidates[:, 1:2]
                        
                        A_x = A[:, 0]
                        A_y = A[:, 1]
                        
                        areas_incl = 0.5 * np.abs(
                            dx * (cand_y - A_y) - dy * (cand_x - A_x)
                        ) 
                        
                        # Minimum area of triangles involving the candidate point
                        min_areas_incl = np.min(areas_incl, axis=1) 
                        
                        # Minimum area of triangles involving the current point
                        curr_area_incl = 0.5 * np.abs(
                            dx * (points[i, 1] - A_y) - dy * (points[i, 0] - A_x)
                        )
                        curr_min_area_incl = np.min(curr_area_incl)
                        
                        # Strictly improve the true minimum area of triangles involving that point
                        best_cand_idx = np.argmax(min_areas_incl)
                        if min_areas_incl[best_cand_idx] > curr_min_area_incl + 1e-12:
                            points[i] = valid_candidates[best_cand_idx]
                            improved = True
                
                # Halve the step size for finer local search
                step_size *= 0.5
                
            # Phase 2: SLSQP exact polishing
            # Extract current configuration and topologies (signs of determinants)
            A_pts = points[idx_A]
            B_pts = points[idx_B]
            C_pts = points[idx_C]
            
            det = (B_pts[:, 0] - A_pts[:, 0]) * (C_pts[:, 1] - A_pts[:, 1]) - (B_pts[:, 1] - A_pts[:, 1]) * (C_pts[:, 0] - A_pts[:, 0])
            signs_array = np.sign(det)
            signs_array[signs_array == 0] = 1  # Handle collinearity safely
            
            min_a = np.min(0.5 * np.abs(det))
            
            x0 = np.zeros(2 * n + 1)
            x0[:-1] = points.flatten()
            x0[-1] = min_a
            
            # NLP Formulation: Maximize z (minimize -z) subject to area constraints and containment constraints
            def area_constraints(x):
                pts = x[:-1].reshape((n, 2))
                z = x[-1]
                A_pts = pts[idx_A]
                B_pts = pts[idx_B]
                C_pts = pts[idx_C]
                det_x = (B_pts[:, 0] - A_pts[:, 0]) * (C_pts[:, 1] - A_pts[:, 1]) - (B_pts[:, 1] - A_pts[:, 1]) * (C_pts[:, 0] - A_pts[:, 0])
                return 0.5 * signs_array * det_x - z
                
            def area_constraints_jac(x):
                pts = x[:-1].reshape((n, 2))
                jac = np.zeros((num_triplets, 2 * n + 1))
                A_pts = pts[idx_A]
                B_pts = pts[idx_B]
                C_pts = pts[idx_C]
                
                coef = 0.5 * signs_array
                
                dA_x = coef * (B_pts[:, 1] - C_pts[:, 1])
                dA_y = coef * (C_pts[:, 0] - B_pts[:, 0])
                dB_x = coef * (C_pts[:, 1] - A_pts[:, 1])
                dB_y = coef * (A_pts[:, 0] - C_pts[:, 0])
                dC_x = coef * (A_pts[:, 1] - B_pts[:, 1])
                dC_y = coef * (B_pts[:, 0] - A_pts[:, 0])
                
                jac[row_idx, 2 * idx_A] = dA_x
                jac[row_idx, 2 * idx_A + 1] = dA_y
                jac[row_idx, 2 * idx_B] = dB_x
                jac[row_idx, 2 * idx_B + 1] = dB_y
                jac[row_idx, 2 * idx_C] = dC_x
                jac[row_idx, 2 * idx_C + 1] = dC_y
                
                jac[:, -1] = -1.0
                return jac

            def containment_constraints(x):
                pts = x[:-1].reshape((n, 2))
                c1 = pts[:, 1] - margin
                c2 = sqrt3 * pts[:, 0] - pts[:, 1] - margin
                c3 = sqrt3 * (1.0 - pts[:, 0]) - pts[:, 1] - margin
                return np.concatenate([c1, c2, c3])
                
            cons = [
                {'type': 'ineq', 'fun': area_constraints, 'jac': area_constraints_jac},
                {'type': 'ineq', 'fun': containment_constraints, 'jac': lambda x: containment_jac}
            ]
            
            res = minimize(
                lambda x: -x[-1], 
                x0, 
                jac=lambda x: obj_jac,
                method='SLSQP', 
                bounds=bounds, 
                constraints=cons, 
                options={'maxiter': 200, 'ftol': 1e-9, 'disp': False}
            )
            
            if -res.fun > min_a:
                cand_points = res.x[:-1].reshape((n, 2))
                u_cand = cand_points[:, 0] - cand_points[:, 1] / sqrt3
                v_cand = cand_points[:, 1] / (sqrt3 / 2.0)
                w_cand = 1.0 - u_cand - v_cand
                
                # Check for strict containment mathematically
                if np.all(u_cand >= 1e-11) and np.all(v_cand >= 1e-11) and np.all(w_cand >= 1e-11):
                    A_cand = cand_points[idx_A]
                    B_cand = cand_points[idx_B]
                    C_cand = cand_points[idx_C]
                    det_cand = (B_cand[:, 0] - A_cand[:, 0]) * (C_cand[:, 1] - A_cand[:, 1]) - (B_cand[:, 1] - A_cand[:, 1]) * (C_cand[:, 0] - A_cand[:, 0])
                    true_min_a = np.min(0.5 * np.abs(det_cand))
                    
                    if true_min_a > min_a:
                        final_area = true_min_a
                        final_points = cand_points
                    else:
                        final_area = min_a
                        final_points = points
                else:
                    final_area = min_a
                    final_points = points
            else:
                final_area = min_a
                final_points = points
                
            if final_area > best_overall_area:
                best_overall_area = final_area
                best_overall_points = np.copy(final_points)
                
        # Return points as a 2D NumPy array and the normalized minimum area
        return best_overall_points, best_overall_area / (sqrt3 / 4.0)

    return find_best_placement(n)
# EVOLVE_END

if __name__ == "__main__":
    points, min_area = run_search_point(11)
    # Convert numpy array to list for JSON serialization in manual testing
    if hasattr(points, 'tolist'):
        points = points.tolist()
    print(json.dumps({"points": points, "min_area": min_area}))
```

```python
#!/usr/bin/env python3
"""Initial 11-point construction in an equilateral triangle."""

import json
import itertools
import math
import random

# EVOLVE_START
import numpy as np
from scipy.optimize import minimize

def run_search_point(n=11):
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")
        
    def find_best_placement(n):
        best_overall_points = None
        best_overall_area = -1

        # Phase 1: 2000 random restarts for coarse broad search
        coarse_restarts = 2000
        # Phase 2 & 3: Top 100 for fine deep search and exact polish
        fine_restarts = 100
        
        # Precompute indices for the pairs formed by the other 10 points
        idx1, idx2 = np.triu_indices(n - 1, k=1)
        
        # Generate coarse candidate offsets (32 angles, 2 radii scales)
        angles_coarse = np.linspace(0, 2*math.pi, 32, endpoint=False)
        base_radii_coarse = np.array([1.0, 0.5])
        grid_r_c, grid_a_c = np.meshgrid(base_radii_coarse, angles_coarse)
        base_cand_offsets_coarse = np.column_stack((
            grid_r_c.flatten() * np.cos(grid_a_c.flatten()),
            grid_r_c.flatten() * np.sin(grid_a_c.flatten())
        ))

        # Generate fine candidate offsets (64 angles, 3 radii scales)
        angles_fine = np.linspace(0, 2*math.pi, 64, endpoint=False)
        base_radii_fine = np.array([1.0, 0.5, 0.25])
        grid_r_f, grid_a_f = np.meshgrid(base_radii_fine, angles_fine)
        base_cand_offsets_fine = np.column_stack((
            grid_r_f.flatten() * np.cos(grid_a_f.flatten()),
            grid_r_f.flatten() * np.sin(grid_a_f.flatten())
        ))
        
        rng = np.random.RandomState(42)
        
        # Precompute triplets for Phase 3 (SLSQP)
        triplets = list(itertools.combinations(range(n), 3))
        num_triplets = len(triplets)
        triplets_array = np.array(triplets)
        idx_A = triplets_array[:, 0]
        idx_B = triplets_array[:, 1]
        idx_C = triplets_array[:, 2]
        row_idx = np.arange(num_triplets)
        
        sqrt3 = math.sqrt(3.0)
        
        # Precompute containment Jacobian and objective Jacobian for SLSQP
        # The variables are [x1, y1, x2, y2, ..., xn, yn, z] where z is the min area.
        containment_jac = np.zeros((3 * n, 2 * n + 1))
        for i in range(n):
            # Derivative of y >= margin wrt y_i
            containment_jac[i, 2 * i + 1] = 1.0
            # Derivative of sqrt(3)*x - y >= margin wrt x_i and y_i
            containment_jac[n + i, 2 * i] = sqrt3
            containment_jac[n + i, 2 * i + 1] = -1.0
            # Derivative of sqrt(3)*(1 - x) - y >= margin wrt x_i and y_i
            containment_jac[2 * n + i, 2 * i] = -sqrt3
            containment_jac[2 * n + i, 2 * i + 1] = -1.0
            
        obj_jac = np.zeros(2 * n + 1)
        obj_jac[-1] = -1.0
        
        margin = 1e-8
        bounds = [(0, 1), (0, sqrt3/2)] * n + [(0, 1)]
        
        coarse_results = []
        
        # 1. Coarse Broad Search
        for restart in range(coarse_restarts):
            # Random initialization strictly inside the equilateral triangle
            points = []
            for _ in range(n):
                u = rng.uniform(0, 1)
                v = rng.uniform(0, 1)
                if u + v > 1.0:
                    u, v = 1.0 - u, 1.0 - v
                # Ensure it starts well inside to respect the margin
                # This guarantees u >= 0.01, v >= 0.01, and u+v <= 0.99
                u = 0.01 + u * 0.97
                v = 0.01 + v * 0.97
                points.append([u + 0.5 * v, sqrt3 * 0.5 * v])
            points = np.array(points)
            
            step_size = 0.1
            # Early stopping at step_size 0.0125
            while step_size >= 0.0125 - 1e-9:
                cand_offsets = base_cand_offsets_coarse * step_size
                
                improved = True
                iters = 0
                # Coordinate descent: optimize one point at a time
                while improved and iters < 50:
                    improved = False
                    iters += 1
                    for i in range(n):
                        others = np.delete(points, i, axis=0)
                        
                        A = others[idx1]
                        B = others[idx2]
                        
                        candidates = points[i] + cand_offsets
                        
                        # Strictly enforce barycentric interior constraints
                        v_cand = candidates[:, 1] / (sqrt3 / 2.0)
                        u_cand = candidates[:, 0] - 0.5 * v_cand
                        valid_mask = (u_cand >= 1e-8) & (v_cand >= 1e-8) & (u_cand + v_cand <= 1 - 1e-8)
                        valid_candidates = candidates[valid_mask]
                        
                        if len(valid_candidates) == 0:
                            continue
                            
                        # Vectorized area calculation for all valid candidates
                        dx = B[:,0] - A[:,0] 
                        dy = B[:,1] - A[:,1] 
                        
                        cand_x = valid_candidates[:, 0:1]
                        cand_y = valid_candidates[:, 1:2]
                        
                        A_x = A[:, 0]
                        A_y = A[:, 1]
                        
                        areas_incl = 0.5 * np.abs(
                            dx * (cand_y - A_y) - dy * (cand_x - A_x)
                        ) 
                        
                        # Minimum area of triangles involving the candidate point
                        min_areas_incl = np.min(areas_incl, axis=1) 
                        
                        # Minimum area of triangles involving the current point
                        curr_area_incl = 0.5 * np.abs(
                            dx * (points[i, 1] - A_y) - dy * (points[i, 0] - A_x)
                        )
                        curr_min_area_incl = np.min(curr_area_incl)
                        
                        # Strictly improve the true minimum area of triangles involving that point
                        best_cand_idx = np.argmax(min_areas_incl)
                        if min_areas_incl[best_cand_idx] > curr_min_area_incl + 1e-12:
                            points[i] = valid_candidates[best_cand_idx]
                            improved = True
                
                # Halve the step size for finer local search
                step_size *= 0.5
                
            # Evaluate the coarse configuration
            A_pts = points[idx_A]
            B_pts = points[idx_B]
            C_pts = points[idx_C]
            det = (B_pts[:, 0] - A_pts[:, 0]) * (C_pts[:, 1] - A_pts[:, 1]) - (B_pts[:, 1] - A_pts[:, 1]) * (C_pts[:, 0] - A_pts[:, 0])
            min_a = np.min(0.5 * np.abs(det))
            coarse_results.append((min_a, points))

        # Select the top 100 configurations to proceed to the fine search
        coarse_results.sort(key=lambda x: x[0], reverse=True)
        top_configurations = coarse_results[:fine_restarts]
        
        # 2. Fine Deep Search & Exact Polish
        for _, init_points in top_configurations:
            points = np.copy(init_points)
            
            # Resume step_size from where coarse search ended
            step_size = 0.0125
            while step_size > 1e-6:
                cand_offsets = base_cand_offsets_fine * step_size
                
                improved = True
                iters = 0
                while improved and iters < 50:
                    improved = False
                    iters += 1
                    for i in range(n):
                        others = np.delete(points, i, axis=0)
                        
                        A = others[idx1]
                        B = others[idx2]
                        
                        candidates = points[i] + cand_offsets
                        
                        v_cand = candidates[:, 1] / (sqrt3 / 2.0)
                        u_cand = candidates[:, 0] - 0.5 * v_cand
                        valid_mask = (u_cand >= 1e-8) & (v_cand >= 1e-8) & (u_cand + v_cand <= 1 - 1e-8)
                        valid_candidates = candidates[valid_mask]
                        
                        if len(valid_candidates) == 0:
                            continue
                            
                        dx = B[:,0] - A[:,0] 
                        dy = B[:,1] - A[:,1] 
                        
                        cand_x = valid_candidates[:, 0:1]
                        cand_y = valid_candidates[:, 1:2]
                        
                        A_x = A[:, 0]
                        A_y = A[:, 1]
                        
                        areas_incl = 0.5 * np.abs(
                            dx * (cand_y - A_y) - dy * (cand_x - A_x)
                        ) 
                        
                        min_areas_incl = np.min(areas_incl, axis=1) 
                        
                        curr_area_incl = 0.5 * np.abs(
                            dx * (points[i, 1] - A_y) - dy * (points[i, 0] - A_x)
                        )
                        curr_min_area_incl = np.min(curr_area_incl)
                        
                        best_cand_idx = np.argmax(min_areas_incl)
                        if min_areas_incl[best_cand_idx] > curr_min_area_incl + 1e-12:
                            points[i] = valid_candidates[best_cand_idx]
                            improved = True
                
                step_size *= 0.5
                
            # 3. Exact polishing with SLSQP
            # Extract current configuration and topologies (signs of determinants)
            A_pts = points[idx_A]
            B_pts = points[idx_B]
            C_pts = points[idx_C]
            
            det = (B_pts[:, 0] - A_pts[:, 0]) * (C_pts[:, 1] - A_pts[:, 1]) - (B_pts[:, 1] - A_pts[:, 1]) * (C_pts[:, 0] - A_pts[:, 0])
            signs_array = np.sign(det)
            signs_array[signs_array == 0] = 1  # Handle collinearity safely
            
            min_a = np.min(0.5 * np.abs(det))
            
            x0 = np.zeros(2 * n + 1)
            x0[:-1] = points.flatten()
            x0[-1] = min_a
            
            # NLP Formulation: Maximize z (minimize -z) subject to area constraints and containment constraints
            def area_constraints(x):
                pts = x[:-1].reshape((n, 2))
                z = x[-1]
                A_pts = pts[idx_A]
                B_pts = pts[idx_B]
                C_pts = pts[idx_C]
                det_x = (B_pts[:, 0] - A_pts[:, 0]) * (C_pts[:, 1] - A_pts[:, 1]) - (B_pts[:, 1] - A_pts[:, 1]) * (C_pts[:, 0] - A_pts[:, 0])
                return 0.5 * signs_array * det_x - z
                
            def area_constraints_jac(x):
                pts = x[:-1].reshape((n, 2))
                jac = np.zeros((num_triplets, 2 * n + 1))
                A_pts = pts[idx_A]
                B_pts = pts[idx_B]
                C_pts = pts[idx_C]
                
                coef = 0.5 * signs_array
                
                dA_x = coef * (B_pts[:, 1] - C_pts[:, 1])
                dA_y = coef * (C_pts[:, 0] - B_pts[:, 0])
                dB_x = coef * (C_pts[:, 1] - A_pts[:, 1])
                dB_y = coef * (A_pts[:, 0] - C_pts[:, 0])
                dC_x = coef * (A_pts[:, 1] - B_pts[:, 1])
                dC_y = coef * (B_pts[:, 0] - A_pts[:, 0])
                
                jac[row_idx, 2 * idx_A] = dA_x
                jac[row_idx, 2 * idx_A + 1] = dA_y
                jac[row_idx, 2 * idx_B] = dB_x
                jac[row_idx, 2 * idx_B + 1] = dB_y
                jac[row_idx, 2 * idx_C] = dC_x
                jac[row_idx, 2 * idx_C + 1] = dC_y
                
                jac[:, -1] = -1.0
                return jac

            def containment_constraints(x):
                pts = x[:-1].reshape((n, 2))
                c1 = pts[:, 1] - margin
                c2 = sqrt3 * pts[:, 0] - pts[:, 1] - margin
                c3 = sqrt3 * (1.0 - pts[:, 0]) - pts[:, 1] - margin
                return np.concatenate([c1, c2, c3])
                
            cons = [
                {'type': 'ineq', 'fun': area_constraints, 'jac': area_constraints_jac},
                {'type': 'ineq', 'fun': containment_constraints, 'jac': lambda x: containment_jac}
            ]
            
            res = minimize(
                lambda x: -x[-1], 
                x0, 
                jac=lambda x: obj_jac,
                method='SLSQP', 
                bounds=bounds, 
                constraints=cons, 
                options={'maxiter': 200, 'ftol': 1e-9, 'disp': False}
            )
            
            if -res.fun > min_a:
                cand_points = res.x[:-1].reshape((n, 2))
                u_cand = cand_points[:, 0] - cand_points[:, 1] / sqrt3
                v_cand = cand_points[:, 1] / (sqrt3 / 2.0)
                w_cand = 1.0 - u_cand - v_cand
                
                # Check for strict containment mathematically
                if np.all(u_cand >= 1e-11) and np.all(v_cand >= 1e-11) and np.all(w_cand >= 1e-11):
                    A_cand = cand_points[idx_A]
                    B_cand = cand_points[idx_B]
                    C_cand = cand_points[idx_C]
                    det_cand = (B_cand[:, 0] - A_cand[:, 0]) * (C_cand[:, 1] - A_cand[:, 1]) - (B_cand[:, 1] - A_cand[:, 1]) * (C_cand[:, 0] - A_cand[:, 0])
                    true_min_a = np.min(0.5 * np.abs(det_cand))
                    
                    if true_min_a > min_a:
                        final_area = true_min_a
                        final_points = cand_points
                    else:
                        final_area = min_a
                        final_points = points
                else:
                    final_area = min_a
                    final_points = points
            else:
                final_area = min_a
                final_points = points
                
            if final_area > best_overall_area:
                best_overall_area = final_area
                best_overall_points = np.copy(final_points)
                
        # Return points as a 2D NumPy array and the normalized minimum area
        return best_overall_points, best_overall_area / (sqrt3 / 4.0)

    return find_best_placement(n)
# EVOLVE_END

if __name__ == "__main__":
    points, min_area = run_search_point(11)
    # Convert numpy array to list for JSON serialization in manual testing
    if hasattr(points, 'tolist'):
        points = points.tolist()
    print(json.dumps({"points": points, "min_area": min_area}))
```

```python
#!/usr/bin/env python3
"""Initial 11-point construction in an equilateral triangle."""

import json
import itertools
import math
import random


# EVOLVE_START
def run_search_point(n=11):
    """Search for a high-quality 11-point Heilbronn configuration."""
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")

    rng = random.Random(271828)
    eps = 2.0e-5
    triples = list(itertools.combinations(range(n), 3))

    def normalize(row):
        s = sum(row)
        return [max(eps, v / s) for v in row]

    def determinants(rows):
        result = []
        for i, j, k in triples:
            a, b, c = rows[i], rows[j], rows[k]
            result.append(
                a[0] * (b[1] * c[2] - b[2] * c[1])
                - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0])
            )
        return result

    def quality(rows):
        values = sorted(abs(x) for x in determinants(rows))
        count = min(18, len(values))
        return values[0], sum(values[:count]) / count

    def better(a, b):
        return a[0] > b[0] or (a[0] == b[0] and a[1] > b[1])

    def random_rows(concentration):
        rows = []
        for _ in range(n):
            values = [
                -math.log(max(1.0e-14, rng.random())) / concentration
                for _ in range(3)
            ]
            total = sum(values)
            rows.append([v / total for v in values])
        return rows

    def lattice_rows():
        level = 7
        pool = []
        for a in range(1, level):
            for b in range(1, level - a):
                c = level - a - b
                pool.append([a / level, b / level, c / level])
        rng.shuffle(pool)
        rows = [pool[i][:] for i in range(n)]
        for row in rows:
            noise = [rng.uniform(-0.035, 0.035) for _ in range(3)]
            mean = sum(noise) / 3.0
            row[:] = normalize(
                [row[q] + noise[q] - mean for q in range(3)]
            )
        return rows

    def folded_sequence():
        rows = []
        for index in range(1, n + 1):
            values = []
            for base in (2, 3, 5):
                value = 0.0
                factor = 1.0 / base
                m = index
                while m:
                    value += (m % base) * factor
                    m //= base
                    factor /= base
                values.append(0.08 + 0.84 * (2.0 * abs(value - 0.5)))
            rows.append(normalize(values))
        return rows

    def polish(rows, current, steps=None):
        """Coordinate-transfer active-set polish."""
        rows = [r[:] for r in rows]
        score = current
        if steps is None:
            steps = (0.006, 0.002, 0.0007)

        for step in steps:
            while True:
                ds = determinants(rows)
                active = sorted(
                    range(len(ds)), key=lambda z: abs(ds[z])
                )[:min(14, len(ds))]
                involved = set()
                for z in active:
                    involved.update(triples[z])

                changed = False
                for i in sorted(involved):
                    for p, q in (
                        (0, 1), (0, 2), (1, 0),
                        (1, 2), (2, 0), (2, 1),
                    ):
                        amount = min(step, rows[i][p] - eps)
                        if amount <= 0.0:
                            continue
                        trial = [r[:] for r in rows]
                        trial[i][p] -= amount
                        trial[i][q] += amount
                        if min(trial[i]) <= eps:
                            continue
                        trial_score = quality(trial)
                        if better(trial_score, score):
                            rows, score = trial, trial_score
                            changed = True
                            break
                    if changed:
                        break
                if not changed:
                    break
        return rows, score

    def anneal(start, iterations=13500):
        rows = [r[:] for r in start]
        best = [r[:] for r in rows]
        current = quality(rows)
        best_quality = current
        stagnant = 0
        last_polish = -1000

        for iteration in range(iterations):
            ds = determinants(rows)
            active = sorted(
                range(len(ds)), key=lambda z: abs(ds[z])
            )[:min(14, len(ds))]

            if rng.random() < 0.88:
                i = rng.choice(triples[rng.choice(active)])
            else:
                i = rng.randrange(n)

            p, q = rng.sample(range(3), 2)
            old = rows[i][:]
            fraction = iteration / float(max(1, iterations))
            remaining = 1.0 - fraction
            scale = 0.115 * remaining ** 1.45 + 0.0015
            delta = rng.uniform(-scale, scale)

            if delta >= 0:
                limit = min(rows[i][q] - eps, 1.0 - eps - rows[i][p])
            else:
                limit = min(rows[i][p] - eps, 1.0 - eps - rows[i][q])
            if limit <= 0:
                continue
            delta = max(-limit, min(limit, delta))

            rows[i][p] += delta
            rows[i][q] -= delta
            if min(rows[i]) <= eps:
                rows[i] = old
                continue

            new_quality = quality(rows)
            old_key = current[0] + 0.035 * current[1]
            new_key = new_quality[0] + 0.035 * new_quality[1]
            temperature = 0.0017 * remaining + 1.0e-7
            accept = new_key >= old_key
            if not accept:
                accept = rng.random() < math.exp(
                    (new_key - old_key) / temperature
                )

            improved = False
            if accept:
                current = new_quality
                if better(new_quality, best_quality):
                    best = [r[:] for r in rows]
                    best_quality = new_quality
                    stagnant = 0
                    improved = True
                else:
                    stagnant += 1
            else:
                rows[i] = old
                stagnant += 1

            if iteration % 250 == 0 or (
                improved and iteration - last_polish >= 40
            ):
                rows, current = polish(rows, current)
                last_polish = iteration
                if better(current, best_quality):
                    best = [r[:] for r in rows]
                    best_quality = current
                    stagnant = 0

            if stagnant >= 1450:
                rows = [r[:] for r in best]
                ds = determinants(rows)
                active = sorted(
                    range(len(ds)), key=lambda z: abs(ds[z])
                )[:min(14, len(ds))]
                counts = [0] * n
                for z in active:
                    for vertex in triples[z]:
                        counts[vertex] += 1
                focus = max(range(n), key=lambda z: counts[z])

                for _ in range(6):
                    p, q = rng.sample(range(3), 2)
                    old_value = rows[focus][:]
                    amount = rng.uniform(-0.028, 0.028)
                    if amount >= 0:
                        limit = min(
                            rows[focus][q] - eps,
                            1.0 - eps - rows[focus][p],
                        )
                    else:
                        limit = min(
                            rows[focus][p] - eps,
                            1.0 - eps - rows[focus][q],
                        )
                    if limit <= 0:
                        continue
                    amount = max(-limit, min(limit, amount))
                    rows[focus][p] += amount
                    rows[focus][q] -= amount
                    if min(rows[focus]) <= eps:
                        rows[focus] = old_value
                current = quality(rows)
                stagnant = 0

        polished_rows, polished_quality = polish(best, best_quality)
        return polished_rows if better(polished_quality, best_quality) else best

    starts = [lattice_rows(), folded_sequence()]
    for concentration in (0.45, 0.75, 1.0, 1.7, 3.0):
        starts.append(random_rows(concentration))
    while len(starts) < 16:
        starts.append(
            lattice_rows()
            if len(starts) % 3 == 0
            else random_rows(0.55 + 2.5 * rng.random())
        )

    candidates = []
    for start in starts:
        candidate = anneal(start)
        candidates.append((quality(candidate), candidate))

    candidates.sort(
        key=lambda item: (item[0][0], item[0][1]), reverse=True
    )

    try:
        import numpy as np
        from scipy.optimize import minimize

        def pack(rows, t):
            return np.array(
                [v for row in rows for v in row[:2]] + [t],
                dtype=float,
            )

        def unpack(vector):
            return [
                [
                    float(vector[2 * i]),
                    float(vector[2 * i + 1]),
                    1.0 - float(vector[2 * i]) - float(vector[2 * i + 1]),
                ]
                for i in range(n)
            ]

        def make_solver(seed):
            ds = determinants(seed)
            signs = [1.0 if d >= 0.0 else -1.0 for d in ds]

            def objective(vector):
                return -vector[-1]

            def objective_jac(vector):
                gradient = np.zeros(2 * n + 1)
                gradient[-1] = -1.0
                return gradient

            def constraint_values(vector):
                rows = unpack(vector)
                values = determinants(rows)
                result = [
                    signs[z] * values[z] - vector[-1]
                    for z in range(len(values))
                ]
                result.extend(row[2] - eps for row in rows)
                return np.asarray(result)

            def constraint_jac(vector):
                rows = unpack(vector)
                matrix = np.zeros((len(triples) + n, 2 * n + 1))
                for z, (i, j, k) in enumerate(triples):
                    ri, rj, rk = rows[i], rows[j], rows[k]
                    for who, u, v in (
                        (i, rj, rk), (j, rk, ri), (k, ri, rj)
                    ):
                        cross0 = u[1] * v[2] - u[2] * v[1]
                        cross1 = u[2] * v[0] - u[0] * v[2]
                        cross2 = u[0] * v[1] - u[1] * v[0]
                        matrix[z, 2 * who] = signs[z] * (cross0 - cross2)
                        matrix[z, 2 * who + 1] = signs[z] * (cross1 - cross2)
                    matrix[z, -1] = -1.0
                for i in range(n):
                    matrix[len(triples) + i, 2 * i] = -1.0
                    matrix[len(triples) + i, 2 * i + 1] = -1.0
                return matrix

            return minimize(
                objective,
                pack(seed, max(0.0, min(abs(d) for d in ds) * 0.92)),
                jac=objective_jac,
                constraints={
                    "type": "ineq",
                    "fun": constraint_values,
                    "jac": constraint_jac,
                },
                bounds=[(eps, 1.0 - eps)] * (2 * n) + [(0.0, 0.2)],
                method="SLSQP",
                options={"maxiter": 900, "ftol": 2.0e-11, "disp": False},
            )

        # Keep four best seeds, then use barycentric diversity for the rest.
        ranked = sorted(
            candidates,
            key=lambda item: (item[0][0], item[0][1]),
            reverse=True,
        )
        selected = ranked[:min(4, len(ranked))]
        remaining = ranked[min(4, len(ranked)):]
        while remaining and len(selected) < min(12, len(ranked)):
            def distance(item):
                vector = [v for row in item[1] for v in row]
                return min(
                    sum((a - b) ** 2 for a, b in zip(
                        vector, [v for row in other[1] for v in row]
                    ))
                    for other in selected
                )
            chosen = max(remaining, key=distance)
            selected.append(chosen)
            remaining.remove(chosen)

        polished = []
        for _, seed in selected:
            first = make_solver(seed)
            if not (first.success or first.x[-1] > 0):
                continue

            raw = unpack(first.x)
            raw_score = quality(raw)
            refined, refined_score = polish(
                raw, raw_score, steps=(0.002, 0.0007, 0.00022, 0.00007)
            )
            if better(refined_score, raw_score):
                raw, raw_score = refined, refined_score

            second = make_solver(raw)
            second_rows = unpack(second.x)
            second_score = quality(second_rows)

            if better(second_score, raw_score):
                polished.append(second_rows)
                continue

            # Controlled escape from a stalled local SLSQP solution.
            perturbed = [row[:] for row in raw]
            active = sorted(
                range(len(triples)),
                key=lambda z: abs(determinants(raw)[z])
            )[:min(14, len(triples))]
            involved = set()
            for z in active:
                involved.update(triples[z])
            for i in involved:
                shift = rng.uniform(-2.0e-5, 2.0e-5)
                perturbed[i][0] += shift
                perturbed[i][1] -= shift
                if min(perturbed[i]) <= eps:
                    perturbed[i] = raw[i][:]
            retry = make_solver(perturbed)
            retry_rows = unpack(retry.x)
            retry_score = quality(retry_rows)
            if better(retry_score, raw_score):
                polished.append(retry_rows)
            else:
                polished.append(raw)

        candidates.extend((quality(row), row) for row in polished)
    except Exception:
        pass

    candidates.sort(
        key=lambda item: (item[0][0], item[0][1]), reverse=True
    )
    rows = candidates[0][1]

    for step in (0.002, 0.0007, 0.00022, 0.00007):
        changed = True
        while changed:
            changed = False
            before = quality(rows)
            for i in range(n):
                for p, q in ((0, 1), (0, 2), (1, 2)):
                    for sign in (-1.0, 1.0):
                        if rows[i][q] - sign * step <= eps:
                            continue
                        trial = [r[:] for r in rows]
                        trial[i][p] += sign * step
                        trial[i][q] -= sign * step
                        if min(trial[i]) <= eps:
                            continue
                        after = quality(trial)
                        if better(after, before):
                            rows, before, changed = trial, after, True

    rows = [normalize(row) for row in rows]
    points = [
        [
            row[1] + 0.5 * row[2],
            math.sqrt(3.0) / 2.0 * row[2],
        ]
        for row in rows
    ]
    min_area = min(
        abs(
            (points[j][0] - points[i][0]) * (points[k][1] - points[i][1])
            - (points[j][1] - points[i][1]) * (points[k][0] - points[i][0])
        ) * 2.0 / math.sqrt(3.0)
        for i, j, k in triples
    )
    return points, float(min_area)
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))
```

```python
#!/usr/bin/env python3
"""Initial 11-point construction in an equilateral triangle."""

import json
import itertools
import math
import random


# EVOLVE_START
def run_search_point(n=11):
    """Search for a high-quality 11-point Heilbronn configuration."""
    if n != 11:
        raise ValueError("the benchmark evaluates n=11")

    rng = random.Random(271828)
    eps = 2.0e-5
    final_margin = 2.0e-7
    triples = list(itertools.combinations(range(n), 3))

    def normalize(row, margin=eps):
        values = [max(margin, float(v)) for v in row]
        total = sum(values)
        return [v / total for v in values]

    def determinants(rows):
        result = []
        for i, j, k in triples:
            a, b, c = rows[i], rows[j], rows[k]
            result.append(
                a[0] * (b[1] * c[2] - b[2] * c[1])
                - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0])
            )
        return result

    def quality(rows):
        values = sorted(abs(v) for v in determinants(rows))
        count = min(18, len(values))
        return values[0], sum(values[:count]) / count

    def better(a, b):
        return a[0] > b[0] or (a[0] == b[0] and a[1] > b[1])

    def random_rows(concentration):
        result = []
        for _ in range(n):
            values = [
                -math.log(max(1.0e-14, rng.random())) / concentration
                for _ in range(3)
            ]
            result.append(normalize(values))
        return result

    def lattice_rows():
        level = 7
        pool = []
        for a in range(1, level):
            for b in range(1, level - a):
                pool.append([a / level, b / level, (level - a - b) / level])
        rng.shuffle(pool)
        result = []
        for row in pool[:n]:
            noise = [rng.uniform(-0.035, 0.035) for _ in range(3)]
            mean = sum(noise) / 3.0
            result.append(normalize([row[i] + noise[i] - mean for i in range(3)]))
        return result

    def folded_sequence():
        result = []
        for index in range(1, n + 1):
            values = []
            for base in (2, 3, 5):
                value = 0.0
                factor = 1.0 / base
                m = index
                while m:
                    value += (m % base) * factor
                    m //= base
                    factor /= base
                values.append(0.08 + 0.84 * (2.0 * abs(value - 0.5)))
            result.append(normalize(values))
        return result

    def polish(rows, current, steps=None, margin=eps):
        rows = [r[:] for r in rows]
        if steps is None:
            steps = (0.006, 0.002, 0.0007)
        score = current

        for step in steps:
            while True:
                ds = determinants(rows)
                active = sorted(range(len(ds)), key=lambda z: abs(ds[z]))[:14]
                involved = set()
                for z in active:
                    involved.update(triples[z])

                changed = False
                for i in sorted(involved):
                    for p, q in (
                        (0, 1), (0, 2), (1, 0),
                        (1, 2), (2, 0), (2, 1),
                    ):
                        amount = min(step, rows[i][p] - margin)
                        if amount <= 0.0:
                            continue
                        trial = [r[:] for r in rows]
                        trial[i][p] -= amount
                        trial[i][q] += amount
                        if min(trial[i]) <= margin:
                            continue
                        trial_score = quality(trial)
                        if better(trial_score, score):
                            rows, score = trial, trial_score
                            changed = True
                            break
                    if changed:
                        break
                if not changed:
                    break
        return rows, score

    def anneal(start, iterations=13500):
        rows = [r[:] for r in start]
        best = [r[:] for r in rows]
        current = quality(rows)
        best_quality = current
        stagnant = 0
        last_polish = -1000

        for iteration in range(iterations):
            ds = determinants(rows)
            active = sorted(range(len(ds)), key=lambda z: abs(ds[z]))[:14]
            if rng.random() < 0.88:
                i = rng.choice(triples[rng.choice(active)])
            else:
                i = rng.randrange(n)

            p, q = rng.sample(range(3), 2)
            old = rows[i][:]
            fraction = iteration / float(max(1, iterations))
            remaining = 1.0 - fraction
            scale = 0.115 * remaining ** 1.45 + 0.0015
            delta = rng.uniform(-scale, scale)

            if delta >= 0.0:
                limit = min(rows[i][q] - eps, 1.0 - eps - rows[i][p])
            else:
                limit = min(rows[i][p] - eps, 1.0 - eps - rows[i][q])
            if limit <= 0.0:
                continue
            delta = max(-limit, min(limit, delta))
            rows[i][p] += delta
            rows[i][q] -= delta

            if min(rows[i]) <= eps:
                rows[i] = old
                continue

            new_quality = quality(rows)
            old_key = current[0] + 0.035 * current[1]
            new_key = new_quality[0] + 0.035 * new_quality[1]
            temperature = 0.0017 * remaining + 1.0e-7
            accept = new_key >= old_key
            if not accept:
                accept = rng.random() < math.exp(
                    (new_key - old_key) / temperature
                )

            improved = False
            if accept:
                current = new_quality
                if better(new_quality, best_quality):
                    best = [r[:] for r in rows]
                    best_quality = new_quality
                    stagnant = 0
                    improved = True
                else:
                    stagnant += 1
            else:
                rows[i] = old
                stagnant += 1

            if iteration % 250 == 0 or (
                improved and iteration - last_polish >= 40
            ):
                rows, current = polish(rows, current)
                last_polish = iteration
                if better(current, best_quality):
                    best = [r[:] for r in rows]
                    best_quality = current
                    stagnant = 0

            if stagnant >= 1450:
                rows = [r[:] for r in best]
                ds = determinants(rows)
                active = sorted(range(len(ds)), key=lambda z: abs(ds[z]))[:14]
                counts = [0] * n
                for z in active:
                    for vertex in triples[z]:
                        counts[vertex] += 1
                focus = max(range(n), key=lambda z: counts[z])

                for _ in range(6):
                    p, q = rng.sample(range(3), 2)
                    old_value = rows[focus][:]
                    amount = rng.uniform(-0.028, 0.028)
                    if amount >= 0.0:
                        limit = min(
                            rows[focus][q] - eps,
                            1.0 - eps - rows[focus][p],
                        )
                    else:
                        limit = min(
                            rows[focus][p] - eps,
                            1.0 - eps - rows[focus][q],
                        )
                    if limit <= 0.0:
                        continue
                    amount = max(-limit, min(limit, amount))
                    rows[focus][p] += amount
                    rows[focus][q] -= amount
                    if min(rows[focus]) <= eps:
                        rows[focus] = old_value
                current = quality(rows)
                stagnant = 0

        polished_rows, polished_quality = polish(best, best_quality)
        return polished_rows if better(polished_quality, best_quality) else best

    starts = [lattice_rows(), folded_sequence()]
    for concentration in (0.45, 0.75, 1.0, 1.7, 3.0):
        starts.append(random_rows(concentration))
    while len(starts) < 16:
        starts.append(
            lattice_rows()
            if len(starts) % 3 == 0
            else random_rows(0.55 + 2.5 * rng.random())
        )

    candidates = []
    for start in starts:
        candidate = anneal(start)
        candidates.append((quality(candidate), candidate))
    candidates.sort(key=lambda item: item[0], reverse=True)

    try:
        import numpy as np
        from scipy.optimize import minimize

        def pack(rows, t):
            return np.array(
                [v for row in rows for v in row[:2]] + [t],
                dtype=float,
            )

        def unpack(vector):
            return [
                [
                    float(vector[2 * i]),
                    float(vector[2 * i + 1]),
                    1.0 - float(vector[2 * i]) - float(vector[2 * i + 1]),
                ]
                for i in range(n)
            ]

        def make_solver(seed, margin=eps, initial_t=None):
            ds = determinants(seed)
            signs = [1.0 if d >= 0.0 else -1.0 for d in ds]
            if initial_t is None:
                initial_t = max(0.0, min(abs(d) for d in ds) * 0.92)

            def objective(vector):
                return -vector[-1]

            def objective_jac(vector):
                gradient = np.zeros(2 * n + 1)
                gradient[-1] = -1.0
                return gradient

            def constraint_values(vector):
                rows = unpack(vector)
                values = determinants(rows)
                result = [
                    signs[z] * values[z] - vector[-1]
                    for z in range(len(values))
                ]
                result.extend(row[2] - margin for row in rows)
                return np.asarray(result)

            def constraint_jac(vector):
                rows = unpack(vector)
                matrix = np.zeros((len(triples) + n, 2 * n + 1))
                for z, (i, j, k) in enumerate(triples):
                    ri, rj, rk = rows[i], rows[j], rows[k]
                    for who, u, v in (
                        (i, rj, rk), (j, rk, ri), (k, ri, rj)
                    ):
                        cross0 = u[1] * v[2] - u[2] * v[1]
                        cross1 = u[2] * v[0] - u[0] * v[2]
                        cross2 = u[0] * v[1] - u[1] * v[0]
                        matrix[z, 2 * who] = signs[z] * (cross0 - cross2)
                        matrix[z, 2 * who + 1] = signs[z] * (cross1 - cross2)
                    matrix[z, -1] = -1.0

                for i in range(n):
                    matrix[len(triples) + i, 2 * i] = -1.0
                    matrix[len(triples) + i, 2 * i + 1] = -1.0
                return matrix

            return minimize(
                objective,
                pack(seed, initial_t),
                jac=objective_jac,
                constraints={
                    "type": "ineq",
                    "fun": constraint_values,
                    "jac": constraint_jac,
                },
                bounds=[(margin, 1.0 - margin)] * (2 * n) + [(0.0, 0.2)],
                method="SLSQP",
                options={
                    "maxiter": 900,
                    "ftol": 2.0e-11,
                    "disp": False,
                },
            )

        ranked = sorted(
            candidates,
            key=lambda item: (item[0][0], item[0][1]),
            reverse=True,
        )
        selected = ranked[:min(4, len(ranked))]
        remaining = ranked[min(4, len(ranked)):]

        while remaining and len(selected) < min(12, len(ranked)):
            def distance(item):
                vector = [v for row in item[1] for v in row]
                return min(
                    sum(
                        (a - b) ** 2
                        for a, b in zip(
                            vector,
                            [v for row in other[1] for v in row],
                        )
                    )
                    for other in selected
                )

            chosen = max(remaining, key=distance)
            selected.append(chosen)
            remaining.remove(chosen)

        polished = []
        for _, seed in selected:
            first = make_solver(seed)
            if not (first.success or first.x[-1] > 0.0):
                continue

            raw = unpack(first.x)
            raw_score = quality(raw)
            refined, refined_score = polish(
                raw,
                raw_score,
                steps=(0.002, 0.0007, 0.00022, 0.00007),
            )
            if better(refined_score, raw_score):
                raw, raw_score = refined, refined_score

            second = make_solver(raw)
            if second.success or second.x[-1] > 0.0:
                second_rows = unpack(second.x)
                second_score = quality(second_rows)
            else:
                second_rows, second_score = raw, raw_score

            if better(second_score, raw_score):
                raw, raw_score = second_rows, second_score

            # Continuation pass: retain the topology and all analytic
            # determinant constraints, but permit points to approach the
            # boundary more closely than the conservative first solve.
            relaxed_start_t = max(
                0.0, 0.995 * min(abs(d) for d in determinants(raw))
            )
            relaxed = make_solver(
                raw,
                margin=final_margin,
                initial_t=relaxed_start_t,
            )
            if relaxed.success or relaxed.x[-1] > 0.0:
                relaxed_rows = unpack(relaxed.x)
                relaxed_score = quality(relaxed_rows)
                valid = all(
                    min(row) > final_margin for row in relaxed_rows
                )
                if valid and relaxed_score[0] > raw_score[0]:
                    raw, raw_score = relaxed_rows, relaxed_score

            # A small topology-locked retry is useful when SLSQP terminates
            # at a stationary point without improving the epigraph.
            perturbed = [row[:] for row in raw]
            active = sorted(
                range(len(triples)),
                key=lambda z: abs(determinants(raw)[z]),
            )[:14]
            involved = set()
            for z in active:
                involved.update(triples[z])
            for i in involved:
                shift = rng.uniform(-2.0e-5, 2.0e-5)
                perturbed[i][0] += shift
                perturbed[i][1] -= shift
                if min(perturbed[i]) <= eps:
                    perturbed[i] = raw[i][:]

            retry = make_solver(perturbed)
            if retry.success or retry.x[-1] > 0.0:
                retry_rows = unpack(retry.x)
                retry_score = quality(retry_rows)
                if better(retry_score, raw_score):
                    raw, raw_score = retry_rows, retry_score

            polished.append(raw)

        candidates.extend((quality(row), row) for row in polished)
    except Exception:
        pass

    candidates.sort(key=lambda item: item[0], reverse=True)
    rows = candidates[0][1]

    for step in (0.002, 0.0007, 0.00022, 0.00007):
        changed = True
        while changed:
            changed = False
            before = quality(rows)
            for i in range(n):
                for p, q in ((0, 1), (0, 2), (1, 2)):
                    for sign in (-1.0, 1.0):
                        if rows[i][q] - sign * step <= final_margin:
                            continue
                        trial = [r[:] for r in rows]
                        trial[i][p] += sign * step
                        trial[i][q] -= sign * step
                        if min(trial[i]) <= final_margin:
                            continue
                        after = quality(trial)
                        if better(after, before):
                            rows, before, changed = trial, after, True

    rows = [normalize(row, final_margin) for row in rows]
    points = [
        [
            row[1] + 0.5 * row[2],
            math.sqrt(3.0) / 2.0 * row[2],
        ]
        for row in rows
    ]
    min_area = min(
        abs(
            (points[j][0] - points[i][0]) * (points[k][1] - points[i][1])
            - (points[j][1] - points[i][1]) * (points[k][0] - points[i][0])
        ) * 2.0 / math.sqrt(3.0)
        for i, j, k in triples
    )
    return points, float(min_area)
# EVOLVE_END


if __name__ == "__main__":
    points, min_area = run_search_point(11)
    print(json.dumps({"points": points, "min_area": min_area}))
```
