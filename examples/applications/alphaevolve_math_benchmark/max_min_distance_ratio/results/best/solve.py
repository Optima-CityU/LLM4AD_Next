#!/usr/bin/env python3
"""Initial 16-point planar construction for the max/min distance ratio."""

import json
import numpy as np
from scipy.optimize import minimize

# EVOLVE_START
def get_partitions_exact(n, parts):
    """Generate all partitions of integer n into exactly 'parts' parts."""
    def generate(n, parts, current_max):
        if n == 0 and parts == 0:
            yield []
            return
        if n <= 0 or parts <= 0:
            return
        for i in range(min(n, current_max), 0, -1):
            for p in generate(n - i, parts - 1, i):
                yield [i] + p
    return list(generate(n, parts, n))

def create_ring_topology(partition):
    """Create initial points arranged in concentric rings based on the partition."""
    pts = []
    for i, count in enumerate(partition):
        if i == 0:
            if count == 1:
                pts.append([0.0, 0.0])
                continue
            else:
                r = 0.5
        else:
            r = i * 1.0
            
        angles = np.linspace(0, 2*np.pi, count, endpoint=False)
        phase = np.random.rand() * 2 * np.pi
        angles += phase
        for a in angles:
            pts.append([r * np.cos(a), r * np.sin(a)])
    return np.array(pts)

def generate_hex_patches(n):
    """Generate initial point configurations based on hexagonal lattices."""
    v1 = np.array([1.0, 0.0])
    v2 = np.array([0.5, np.sqrt(3)/2])
    
    points = []
    # Generate a large enough grid
    for i in range(-10, 11):
        for j in range(-10, 11):
            points.append(i * v1 + j * v2)
    points = np.array(points)
    
    patches = []
    
    # 1. Distance-based patches from different symmetry centers
    centers = [
        np.array([0.0, 0.0]),                   # On a vertex
        (v1 + v2) / 3.0,                        # Center of a triangle
        v1 / 2.0,                               # Center of an edge
        (v1 + 2*v2) / 3.0                       # Center of other triangle
    ]
    
    for c in centers:
        dists = np.linalg.norm(points - c, axis=1)
        idx = np.argsort(dists)
        patches.append(points[idx[:n]])
        
    # 2. A 4x4 rhombus (if n=16)
    if n == 16:
        rhombus = []
        for i in range(4):
            for j in range(4):
                rhombus.append(i * v1 + j * v2)
        patches.append(np.array(rhombus))
        
        # 3. Hexagon-like subsets (19 points in radius 2 hexagon, pick 16)
        h3 = []
        for i in range(-2, 3):
            for j in range(-2, 3):
                if -2 <= i + j <= 2:
                    h3.append(i * v1 + j * v2)
        h3 = np.array(h3)
        
        # Add random subsets of 16 points out of the 19 points
        for _ in range(10):
            idx = np.random.choice(len(h3), n, replace=False)
            patches.append(h3[idx])

    return patches

def scale_to_min_dist_1(pts):
    """Scale the point set so that the minimum pairwise distance is slightly > 1."""
    n = len(pts)
    idx_i, idx_j = np.triu_indices(n, k=1)
    diff = pts[idx_i] - pts[idx_j]
    dist = np.linalg.norm(diff, axis=1)
    min_d = np.min(dist)
    if min_d > 1e-5:
        # Scale to 1.001 to ensure strict feasibility for constraints
        return pts / min_d * 1.001
    return pts

def optimize_topology(initial_points, maxiter=1000):
    """Optimize a point configuration using SLSQP with an exact Jacobian."""
    n = len(initial_points)
    
    # Center points to remove translational drift
    initial_points = initial_points - np.mean(initial_points, axis=0)
    initial_Z = np.max(np.sum((initial_points[:, None, :] - initial_points[None, :, :])**2, axis=-1))
    
    # Variables: x_1, y_1, ..., x_n, y_n, Z
    x0 = np.zeros(2*n + 1)
    x0[:2*n] = initial_points.flatten()
    x0[-1] = initial_Z
    
    def obj(x):
        return x[-1]
    
    def obj_jac(x):
        grad = np.zeros_like(x)
        grad[-1] = 1.0
        return grad

    idx_i, idx_j = np.triu_indices(n, k=1)
    num_pairs = len(idx_i)
    
    def constraints(x):
        pts = x[:2*n].reshape((n, 2))
        Z = x[-1]
        
        diff = pts[idx_i] - pts[idx_j]
        dist_sq = np.sum(diff**2, axis=1)
        
        c_min = dist_sq - 1.0
        c_max = Z - dist_sq
        
        return np.concatenate([c_min, c_max])
        
    def constraints_jac(x):
        pts = x[:2*n].reshape((n, 2))
        
        diff = pts[idx_i] - pts[idx_j]
        
        jac_min = np.zeros((num_pairs, 2*n + 1))
        jac_max = np.zeros((num_pairs, 2*n + 1))
        
        grad_pi = 2 * diff
        grad_pj = -2 * diff
        
        row_idx = np.arange(num_pairs)
        jac_min[row_idx, idx_i*2] = grad_pi[:, 0]
        jac_min[row_idx, idx_i*2+1] = grad_pi[:, 1]
        jac_min[row_idx, idx_j*2] = grad_pj[:, 0]
        jac_min[row_idx, idx_j*2+1] = grad_pj[:, 1]
        
        jac_max[row_idx, idx_i*2] = -grad_pi[:, 0]
        jac_max[row_idx, idx_i*2+1] = -grad_pi[:, 1]
        jac_max[row_idx, idx_j*2] = -grad_pj[:, 0]
        jac_max[row_idx, idx_j*2+1] = -grad_pj[:, 1]
        jac_max[:, -1] = 1.0
        
        return np.vstack([jac_min, jac_max])

    cons = {'type': 'ineq', 'fun': constraints, 'jac': constraints_jac}
    bounds = [(None, None)] * (2*n) + [(1.0, None)]
    
    res = minimize(obj, x0, method='SLSQP', jac=obj_jac, constraints=cons, bounds=bounds, 
                   options={'maxiter': maxiter, 'ftol': 1e-8, 'disp': False})
    
    final_pts = res.x[:2*n].reshape((n, 2))
    
    # Calculate true R^2 accurately
    dist_matrix = np.sum((final_pts[:, None, :] - final_pts[None, :, :])**2, axis=-1)
    np.fill_diagonal(dist_matrix, np.inf)
    min_d2 = np.min(dist_matrix)
    
    dist_matrix[dist_matrix == np.inf] = 0
    max_d2 = np.max(dist_matrix)
    
    true_Z = max_d2 / min_d2 if min_d2 > 1e-5 else np.inf
    is_feasible = (min_d2 > 1e-5)
    
    return final_pts, true_Z, is_feasible

def perturb(pts, strategy):
    """Apply a continuous perturbation to escape local minima."""
    pts = pts.copy()
    if strategy == 'compression':
        # Diameter compression: shrink the maximum distance pair
        dist_matrix = np.sum((pts[:, None, :] - pts[None, :, :])**2, axis=-1)
        i, j = np.unravel_index(np.argmax(dist_matrix), dist_matrix.shape)
        center = (pts[i] + pts[j]) / 2
        pts[i] = pts[i] + 0.05 * (center - pts[i])
        pts[j] = pts[j] + 0.05 * (center - pts[j])
    elif strategy == 'isolation':
        # Isolation-reduction: move the most isolated point closer to neighbors
        dist_matrix = np.sum((pts[:, None, :] - pts[None, :, :])**2, axis=-1)
        np.fill_diagonal(dist_matrix, np.inf)
        min_dists = np.min(dist_matrix, axis=1)
        isolated_idx = np.argmax(min_dists)
        nearest_3 = np.argsort(dist_matrix[isolated_idx])[:3]
        centroid = np.mean(pts[nearest_3], axis=0)
        pts[isolated_idx] = pts[isolated_idx] + 0.1 * (centroid - pts[isolated_idx])
    elif strategy == 'jitter':
        # Random jittering
        pts += np.random.normal(0, 0.05, pts.shape)
    return pts

def optimize_construct(n=16, d=2):
    if (n, d) != (16, 2):
        raise ValueError("the benchmark evaluates n=16 and d=2")
        
    best_Z = np.inf
    best_pts = None
    
    # Initialize with 34 distinct ring-packing topologies (partitions of 16 into 4 parts)
    # Plus partitions into 3, 2, and 1 parts for comprehensiveness
    partitions = get_partitions_exact(16, 4) + \
                 get_partitions_exact(16, 3) + \
                 get_partitions_exact(16, 2) + \
                 get_partitions_exact(16, 1)
                 
    elites = []
    
    # Evaluate all topological priors (Rings)
    for part in partitions:
        part = part[::-1] # Order smallest to largest ring
        pts = create_ring_topology(part)
        pts = scale_to_min_dist_1(pts)
        pts += np.random.normal(0, 0.01, pts.shape) # Symmetry breaking
        
        opt_pts, Z, success = optimize_topology(pts)
        
        if success:
            if Z < best_Z:
                best_Z = Z
                best_pts = opt_pts
            elites.append((Z, opt_pts))

    # Evaluate hexagonal lattice patches
    hex_patches = generate_hex_patches(n)
    for pts in hex_patches:
        pts = scale_to_min_dist_1(pts)
        pts += np.random.normal(0, 0.01, pts.shape) # Symmetry breaking
        
        opt_pts, Z, success = optimize_topology(pts)
        
        if success:
            if Z < best_Z:
                best_Z = Z
                best_pts = opt_pts
            elites.append((Z, opt_pts))
            
    # Add pure random configurations
    for _ in range(20):
        pts = np.random.uniform(-2, 2, (16, 2))
        pts = scale_to_min_dist_1(pts)
        opt_pts, Z, success = optimize_topology(pts)
        if success:
            if Z < best_Z:
                best_Z = Z
                best_pts = opt_pts
            elites.append((Z, opt_pts))
            
    # Keep the top 20 elites for memetic Basin Hopping
    elites.sort(key=lambda x: x[0])
    elites = elites[:20]
    
    # Memetic Basin Hopping framework
    num_iterations = 300
    for it in range(num_iterations):
        idx = np.random.randint(len(elites))
        current_Z, current_pts = elites[idx]
        
        strategy = np.random.choice(['compression', 'isolation', 'jitter'])
        new_pts = perturb(current_pts, strategy)
        new_pts = scale_to_min_dist_1(new_pts)
        
        opt_pts, Z, success = optimize_topology(new_pts)
        
        if success:
            if Z < best_Z:
                best_Z = Z
                best_pts = opt_pts
            
            # Replace worst elite if improved
            if Z < elites[-1][0]:
                elites[-1] = (Z, opt_pts)
                elites.sort(key=lambda x: x[0])
                
    # SQP refinement for the absolute best configuration
    if best_pts is not None:
        best_pts, best_Z, _ = optimize_topology(best_pts, maxiter=2000)
    else:
        # Fallback
        best_pts = np.random.uniform(-2, 2, (16, 2))
        best_Z = 100.0
    
    return best_pts, float(best_Z)
# EVOLVE_END

if __name__ == "__main__":
    points, _ = optimize_construct(16, 2)
    print(json.dumps({"points": points.tolist()}))