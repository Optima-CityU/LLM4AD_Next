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