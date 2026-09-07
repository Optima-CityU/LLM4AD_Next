Two-phase approach: lattice subgraph selection initializes a physics-based compression that surpasses rigid-lattice limits in 11-hexagon packing.

- Hybrid Lattice-Physics Optimization: Starting from an exhaustive enumeration of connected 11-hexagon honeycomb subgraphs to pick the minimal-bounding configuration, then running a physics-based compression with repulsive-overlap forces and inward boundary pressure from that lattice initialization, enabled symmetry slip and compression below the rigid-lattice lower bound (~4.0), achieving outer_hex_side_length 3.941939153759562 with validity 1.0 and target_ratio 0.9972249308442194; future designs should reuse lattice-informed initializations instead of fixed 4-3-4 or 1-6-4 layouts and pair them with continuous force-based compression to escape lattice constraints.

```python
#!/usr/bin/env python3
"""Initial candidate for packing 11 unit hexagons in a regular hexagon."""

import json
import math
import random
import numpy as np
from scipy.optimize import minimize, basinhopping

EPSILON = 1e-9

def hexagon_vertices(center_x, center_y, side_length, angle_degrees):
    vertices = []
    angle_radians = math.radians(angle_degrees)
    for i in range(6):
        angle = angle_radians + 2 * math.pi * i / 6
        x = center_x + side_length * math.cos(angle)
        y = center_y + side_length * math.sin(angle)
        vertices.append((x, y))
    return vertices

def normalize_vector(v):
    magnitude = math.sqrt(v[0]**2 + v[1]**2)
    return (v[0] / magnitude, v[1] / magnitude) if magnitude > EPSILON else (0., 0.)

def get_normals(vertices):
    normals = []
    for i in range(len(vertices)):
        p1 = vertices[i]
        p2 = vertices[(i + 1) % len(vertices)]
        edge = (p2[0] - p1[0], p2[1] - p1[1])
        normal = normalize_vector((-edge[1], edge[0]))
        normals.append(normal)
    return normals

def project_polygon(vertices, axis):
    min_proj = float('inf')
    max_proj = float('-inf')
    for vertex in vertices:
        projection = vertex[0] * axis[0] + vertex[1] * axis[1]
        min_proj = min(min_proj, projection)
        max_proj = max(max_proj, projection)
    return min_proj, max_proj

def overlap_1d(min1, max1, min2, max2):
    return max1 >= min2 - EPSILON and max2 >= min1 - EPSILON

def polygons_intersect(vertices1, vertices2):
    normals1 = get_normals(vertices1)
    normals2 = get_normals(vertices2)
    axes = normals1 + normals2
    for axis in axes:
        min1, max1 = project_polygon(vertices1, axis)
        min2, max2 = project_polygon(vertices2, axis)
        if not overlap_1d(min1, max1, min2, max2):
            return False
    return True

def hexagons_are_disjoint(hex1_params, hex2_params):
    hex1_vertices = hexagon_vertices(*hex1_params)
    hex2_vertices = hexagon_vertices(*hex2_params)
    return not polygons_intersect(hex1_vertices, hex2_vertices)

def is_inside_hexagon(point, hex_params):
    hex_vertices = hexagon_vertices(*hex_params)
    for i in range(len(hex_vertices)):
        p1 = hex_vertices[i]
        p2 = hex_vertices[(i + 1) % len(hex_vertices)]
        edge_vector = (p2[0] - p1[0], p2[1] - p1[1])
        point_vector = (point[0] - p1[0], point[1] - p1[1])
        cross_product = (edge_vector[0] * point_vector[1] - edge_vector[1] * point_vector[0])
        if cross_product < -EPSILON:
            return False
    return True

def all_hexagons_contained(inner_hex_params_list, outer_hex_params):
    for inner_hex_params in inner_hex_params_list:
        inner_hex_vertices = hexagon_vertices(*inner_hex_params)
        for vertex in inner_hex_vertices:
            if not is_inside_hexagon(vertex, outer_hex_params):
                return False
    return True    

def verify_construction(inner_hex_data, outer_hex_center, outer_hex_side_length, outer_hex_angle_degrees):
    inner_hex_params_list = [
        (x, y, 1, angle) for x, y, angle in inner_hex_data
    ]
    outer_hex_params = (
        outer_hex_center[0], outer_hex_center[1],
        outer_hex_side_length, outer_hex_angle_degrees
    )
    if outer_hex_side_length < EPSILON:
        return False
    for i in range(len(inner_hex_params_list)):
        for j in range(i + 1, len(inner_hex_params_list)):
            if not hexagons_are_disjoint(inner_hex_params_list[i], inner_hex_params_list[j]):
                return False
    if not all_hexagons_contained(inner_hex_params_list, outer_hex_params):
        return False
    return True


# EVOLVE_START
import itertools

def get_r(theta, A, margin=1.000001):
    """
    Calculate the support function (max projection) of a regular hexagon.
    A small margin is added to ensure strict non-overlapping during optimization.
    """
    diff = (theta - A) % (math.pi / 3)
    if diff > math.pi / 6:
        diff -= math.pi / 3
    return math.cos(diff) * margin

def get_lattice_center(q, r):
    """Get Cartesian coordinates for axial honeycomb lattice coordinates."""
    s3 = math.sqrt(3)
    x = q * s3 + r * s3 / 2
    y = r * 1.5
    return x, y

def is_connected(subset):
    """Check if a subset of lattice cells forms a single connected component."""
    subset_set = set(subset)
    start = subset[0]
    visited = {start}
    queue = [start]
    while queue:
        q, r = queue.pop(0)
        for dq, dr in [(1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1)]:
            n = (q + dq, r + dr)
            if n in subset_set and n not in visited:
                visited.add(n)
                queue.append(n)
    return len(visited) == 11

def evaluate_bounding_hexagon(positions, angle_rad):
    """Calculate the minimal outer regular hexagon side length for fixed inner positions."""
    normals = []
    for i in range(3):
        n_angle = angle_rad + i * math.pi / 3
        normals.append((math.cos(n_angle), math.sin(n_angle), n_angle))
        
    max_width = 0.0
    for nx, ny, n_angle in normals:
        min_proj = float('inf')
        max_proj = float('-inf')
        for px, py in positions:
            proj_c = px * nx + py * ny
            r_proj = get_r(n_angle, math.pi / 6, margin=1.0)
            
            if proj_c + r_proj > max_proj:
                max_proj = proj_c + r_proj
            if proj_c - r_proj < min_proj:
                min_proj = proj_c - r_proj
                
        width = max_proj - min_proj
        if width > max_width:
            max_width = width
            
    return max_width / math.sqrt(3)

def get_heuristic_R(positions, current_best):
    """Fast estimation of the minimal bounding hexagon over several angles."""
    best_R = float('inf')
    # Coarse pass to quickly reject unpromising topologies
    for angle_deg in (0, 30):
        angle_rad = math.radians(angle_deg)
        R = evaluate_bounding_hexagon(positions, angle_rad)
        if R < best_R:
            best_R = R
            
    if best_R > current_best * 1.05:
        return best_R
        
    # Fine pass
    for angle_deg in range(0, 60, 5):
        if angle_deg in (0, 30): continue
        angle_rad = math.radians(angle_deg)
        R = evaluate_bounding_hexagon(positions, angle_rad)
        if R < best_R:
            best_R = R
    return best_R

def penalty_objective(flat_vars, C_overlap, C_outer):
    """Objective function for L-BFGS-B that penalizes overlaps."""
    centers = flat_vars[0:22].reshape(11, 2)
    angles = flat_vars[22:33]
    R_out = flat_vars[33]
    A_out = flat_vars[34]
    
    penalty = 0.0
    
    # Pairwise overlaps
    for i in range(11):
        for j in range(i + 1, 11):
            axes = []
            for k in range(3):
                axes.append(angles[i] + math.pi / 6 + k * math.pi / 3)
                axes.append(angles[j] + math.pi / 6 + k * math.pi / 3)
            
            min_overlap = float('inf')
            for theta in axes:
                nx = math.cos(theta)
                ny = math.sin(theta)
                
                proj_i = centers[i, 0] * nx + centers[i, 1] * ny
                r_i = get_r(theta, angles[i])
                min_i = proj_i - r_i
                max_i = proj_i + r_i
                
                proj_j = centers[j, 0] * nx + centers[j, 1] * ny
                r_j = get_r(theta, angles[j])
                min_j = proj_j - r_j
                max_j = proj_j + r_j
                
                overlap = min(max_i, max_j) - max(min_i, min_j)
                if overlap < min_overlap:
                    min_overlap = overlap
            
            if min_overlap > 0:
                penalty += C_overlap * (min_overlap ** 2)
                
    # Outer overlaps
    for k in range(6):
        theta = A_out + math.pi / 6 + k * math.pi / 3
        nx = math.cos(theta)
        ny = math.sin(theta)
        limit = R_out * math.sqrt(3) / 2
        
        for i in range(11):
            proj_i = centers[i, 0] * nx + centers[i, 1] * ny
            r_i = get_r(theta, angles[i])
            max_i = proj_i + r_i
            
            if max_i > limit:
                penalty += C_outer * ((max_i - limit) ** 2)
                
    return R_out + penalty

def objective_slsqp(flat_vars):
    """Objective function for SLSQP (minimize outer radius)."""
    return flat_vars[33]

def constraints_slsqp(flat_vars):
    """Hard constraints for SLSQP enforcing non-overlapping and containment."""
    centers = flat_vars[0:22].reshape(11, 2)
    angles = flat_vars[22:33]
    R_out = flat_vars[33]
    A_out = flat_vars[34]
    
    ineqs = []
    
    # Pairwise non-overlap
    for i in range(11):
        for j in range(i + 1, 11):
            axes = []
            for k in range(3):
                axes.append(angles[i] + math.pi / 6 + k * math.pi / 3)
                axes.append(angles[j] + math.pi / 6 + k * math.pi / 3)
            
            min_overlap = float('inf')
            for theta in axes:
                nx = math.cos(theta)
                ny = math.sin(theta)
                
                proj_i = centers[i, 0] * nx + centers[i, 1] * ny
                r_i = get_r(theta, angles[i])
                min_i = proj_i - r_i
                max_i = proj_i + r_i
                
                proj_j = centers[j, 0] * nx + centers[j, 1] * ny
                r_j = get_r(theta, angles[j])
                min_j = proj_j - r_j
                max_j = proj_j + r_j
                
                overlap = min(max_i, max_j) - max(min_i, min_j)
                if overlap < min_overlap:
                    min_overlap = overlap
            
            ineqs.append(-min_overlap)
            
    # Outer containment
    for k in range(6):
        theta = A_out + math.pi / 6 + k * math.pi / 3
        nx = math.cos(theta)
        ny = math.sin(theta)
        limit = R_out * math.sqrt(3) / 2
        
        for i in range(11):
            proj_i = centers[i, 0] * nx + centers[i, 1] * ny
            r_i = get_r(theta, angles[i])
            max_i = proj_i + r_i
            ineqs.append(limit - max_i)
            
    return np.array(ineqs)

def optimize_construct():
    """
    Construct an arrangement of 11 unit hexagons within a larger regular hexagon.
    Combines exhaustive lattice search to find an optimal topological start,
    followed by continuous physics-based optimization to compress the structure.
    """
    # 1. Generate all valid 11-hexagon connected subgraphs in a radius-2 honeycomb lattice
    cells = []
    for q in range(-2, 3):
        for r in range(-2, 3):
            if abs(q + r) <= 2:
                cells.append((q, r))

    valid_subsets = []
    for subset in itertools.combinations(cells, 11):
        if is_connected(subset):
            valid_subsets.append(subset)
            
    best_heuristic_R = float('inf')
    best_subset = None

    # 2. Evaluate all subgraphs to find the most promising topology
    for subset in valid_subsets:
        positions = [get_lattice_center(q, r) for q, r in subset]
        R = get_heuristic_R(positions, best_heuristic_R)
        if R < best_heuristic_R:
            best_heuristic_R = R
            best_subset = positions

    # Center the best lattice configuration
    cx = sum(p[0] for p in best_subset) / 11
    cy = sum(p[1] for p in best_subset) / 11
    best_subset = [(p[0] - cx, p[1] - cy) for p in best_subset]

    # Find the exact best angle for the chosen subset
    best_angle = 0
    min_R = float('inf')
    for angle_deg in range(60):
        R = evaluate_bounding_hexagon(best_subset, math.radians(angle_deg))
        if R < min_R:
            min_R = R
            best_angle = math.radians(angle_deg)
            
    # 3. Setup continuous optimization
    flat_vars = np.zeros(35)
    flat_vars[0:22] = np.array(best_subset).flatten()
    flat_vars[22:33] = math.pi / 6 # Lattice hexagons start at 30 degrees (flat-topped)
    flat_vars[33] = min_R * 1.02 # Initial outer R with slight slack
    flat_vars[34] = best_angle

    # Break symmetry slightly to allow sliding
    np.random.seed(42)
    flat_vars[0:22] += np.random.uniform(-0.01, 0.01, 22)
    flat_vars[22:33] += np.random.uniform(-0.02, 0.02, 11)

    # Phase 1: Aggressive compression using penalty method (allows temporary soft overlaps)
    for C in [100, 1000, 10000]:
        res = minimize(penalty_objective, flat_vars, args=(C, C), method='L-BFGS-B', options={'maxiter': 2000})
        flat_vars = res.x

    # Phase 2: Exact feasibility using SLSQP
    cons = {'type': 'ineq', 'fun': constraints_slsqp}
    res = minimize(objective_slsqp, flat_vars, method='SLSQP', constraints=cons, options={'maxiter': 1000, 'ftol': 1e-6})
    
    final_vars = res.x
    centers = final_vars[0:22].reshape(11, 2)
    angles = final_vars[22:33]
    R_out = final_vars[33]
    A_out = final_vars[34]
    
    # 4. Cleanup and strict verification guarantee
    # Resolve any floating-point internal overlaps
    max_inner_overlap = 0
    for i in range(11):
        for j in range(i + 1, 11):
            axes = []
            for k in range(3):
                axes.append(angles[i] + math.pi / 6 + k * math.pi / 3)
                axes.append(angles[j] + math.pi / 6 + k * math.pi / 3)
            min_overlap = float('inf')
            for theta in axes:
                nx = math.cos(theta)
                ny = math.sin(theta)
                proj_i = centers[i, 0] * nx + centers[i, 1] * ny
                r_i = get_r(theta, angles[i], margin=1.0)
                min_i = proj_i - r_i
                max_i = proj_i + r_i
                proj_j = centers[j, 0] * nx + centers[j, 1] * ny
                r_j = get_r(theta, angles[j], margin=1.0)
                min_j = proj_j - r_j
                max_j = proj_j + r_j
                overlap = min(max_i, max_j) - max(min_i, min_j)
                if overlap < min_overlap:
                    min_overlap = overlap
            if min_overlap > max_inner_overlap:
                max_inner_overlap = min_overlap
                
    if max_inner_overlap > -1e-9:
        scale = 1.0 + max_inner_overlap + 1e-4
        centers *= scale
        
    # Strictly enforce outer boundary condition
    max_required_R = R_out
    for k in range(6):
        theta = A_out + math.pi / 6 + k * math.pi / 3
        nx = math.cos(theta)
        ny = math.sin(theta)
        for i in range(11):
            proj_i = centers[i, 0] * nx + centers[i, 1] * ny
            r_i = get_r(theta, angles[i], margin=1.0)
            req_R = (proj_i + r_i) / (math.sqrt(3) / 2)
            if req_R > max_required_R:
                max_required_R = req_R
                
    R_out = max_required_R + 1e-5
    
    # Shift to positive coordinates
    shift_x, shift_y = 10.0, 10.0
    
    inner_hexagons = []
    for i in range(11):
        inner_hexagons.append([
            float(centers[i, 0] + shift_x),
            float(centers[i, 1] + shift_y),
            float(math.degrees(angles[i]))
        ])
        
    return inner_hexagons, [shift_x, shift_y], float(R_out), float(math.degrees(A_out))
# EVOLVE_END


if __name__ == "__main__":
    inner, center, side, angle = optimize_construct()
    print(json.dumps({
        "inner_hexagons": inner,
        "outer_center": center,
        "outer_side_length": side,
        "outer_angle_degrees": angle,
    }))
```
