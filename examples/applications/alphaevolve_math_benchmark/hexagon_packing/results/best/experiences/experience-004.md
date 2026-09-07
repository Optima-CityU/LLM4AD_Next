Key design choices and evidence from the evaluated algorithm that yielded a valid, tight packing of 11 unit hexagons in a regular hexagon.

- Accelerated Gauss-Seidel SAT-MTV with Free Drift and Cosine Annealing: In the SAT-guided MTV optimizer for packing 11 unit hexagons, applying Gauss-Seidel constraint resolution with in-place MTV corrections and precomputed vertex offsets, while removing forced centroid re-centering to permit free group drift and using a cosine-annealed learning rate, produced faster, more stable convergence and tighter boundary fits. A 25-step outer-side-length bisection coupled with a 0.25° outer-angle micro-sweep extracted residual slack to meet boundary conditions without overlaps. This configuration achieved validity 1.0 with outer_hex_side_length 3.9601078721733343, target_ratio 0.9926497274536717, and eval_time 116.02776306401938.

```python
#!/usr/bin/env python3
"""Initial candidate for packing 11 unit hexagons in a regular hexagon."""

import json

# EVOLVE_START
import math

EPSILON = 1e-9

def hexagon_vertices(center_x, center_y, side_length, angle_degrees):
    """
    Calculate the vertices of a hexagon.

    Args:
        center_x: x-coordinate of hexagon center
        center_y: y-coordinate of hexagon center
        side_length: Length of hexagon side
        angle_degrees: Rotation angle in degrees

    Returns:
        list: List of (x, y) tuples representing the hexagon vertices
    """
    vertices = []
    angle_radians = math.radians(angle_degrees)
    for i in range(6):
        angle = angle_radians + 2 * math.pi * i / 6
        x = center_x + side_length * math.cos(angle)
        y = center_y + side_length * math.sin(angle)
        vertices.append((x, y))
    return vertices

def normalize_vector(v):
    """
    Normalize a 2D vector to unit length.

    Args:
        v: (x, y) tuple representing the vector

    Returns:
        tuple: Normalized (x, y) vector
    """
    magnitude = math.sqrt(v[0]**2 + v[1]**2)
    return (v[0] / magnitude, v[1] / magnitude) if magnitude > EPSILON else (0., 0.)

def get_normals(vertices):
    """
    Calculate normal vectors for all edges of a polygon.

    Args:
        vertices: List of (x, y) tuples representing polygon vertices

    Returns:
        list: List of (x, y) normal vectors
    """
    normals = []
    for i in range(len(vertices)):
        p1 = vertices[i]
        p2 = vertices[(i + 1) % len(vertices)]
        edge = (p2[0] - p1[0], p2[1] - p1[1])
        normal = normalize_vector((-edge[1], edge[0]))
        normals.append(normal)
    return normals

def project_polygon(vertices, axis):
    """
    Project a polygon onto an axis and return the min and max projection values.

    Args:
        vertices: List of (x, y) tuples representing polygon vertices
        axis: (x, y) tuple representing the projection axis

    Returns:
        tuple: (min_projection, max_projection) projection range
    """
    min_proj = float('inf')
    max_proj = float('-inf')
    for vertex in vertices:
        projection = vertex[0] * axis[0] + vertex[1] * axis[1]
        min_proj = min(min_proj, projection)
        max_proj = max(max_proj, projection)
    return min_proj, max_proj

def overlap_1d(min1, max1, min2, max2):
    """
    Check if two 1D intervals overlap (with epsilon tolerance).

    Args:
        min1, max1: Start and end of first interval
        min2, max2: Start and end of second interval

    Returns:
        bool: True if intervals overlap, False otherwise
    """
    return max1 >= min2 - EPSILON and max2 >= min1 - EPSILON

def polygons_intersect(vertices1, vertices2):
    """
    Check if two convex polygons intersect using the Separating Axis Theorem.

    Args:
        vertices1: List of (x, y) tuples for first polygon
        vertices2: List of (x, y) tuples for second polygon

    Returns:
        bool: True if polygons intersect, False otherwise
    """
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
    """
    Check if two hexagons are disjoint (non-overlapping).

    Args:
        hex1_params: (center_x, center_y, side_length, angle_degrees) for first hexagon
        hex2_params: (center_x, center_y, side_length, angle_degrees) for second hexagon

    Returns:
        bool: True if hexagons are disjoint, False if they intersect
    """   
    hex1_vertices = hexagon_vertices(*hex1_params)
    hex2_vertices = hexagon_vertices(*hex2_params)
    return not polygons_intersect(hex1_vertices, hex2_vertices)

def is_inside_hexagon(point, hex_params):
    """
    Check if a point is inside a hexagon.

    Args:
        point: (x, y) tuple representing the point
        hex_params: (center_x, center_y, side_length, angle_degrees) for the hexagon

    Returns:
        bool: True if point is inside hexagon, False otherwise
    """
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
    """
    Check if all inner hexagons are completely contained within the outer hexagon.

    Args:
        inner_hex_params_list: List of (x, y, side_length, angle_degrees) for inner hexagons
        outer_hex_params: (center_x, center_y, side_length, angle_degrees) for outer hexagon

    Returns:
        bool: True if all inner hexagons are contained, False otherwise
    """
    for inner_hex_params in inner_hex_params_list:
        inner_hex_vertices = hexagon_vertices(*inner_hex_params)
        for vertex in inner_hex_vertices:
            if not is_inside_hexagon(vertex, outer_hex_params):
                return False
    return True    

def verify_construction(inner_hex_data, outer_hex_center, outer_hex_side_length, outer_hex_angle_degrees):
    """
    Verify the complete construction meets all requirements.

    Args:
        inner_hex_data: List of [x, y, angle_degrees] for inner hexagons
        outer_hex_center: [x, y] for outer hexagon center
        outer_hex_side_length: Side length of outer hexagon
        outer_hex_angle_degrees: Rotation angle of outer hexagon in degrees

    Returns:
        bool: True if construction is valid, False otherwise
    """
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

def optimize_construct():
    def optimize_layout(seed_centers, inner_angle, outer_angle, S, max_iters=1500):
        """Highly optimized Physics-based SAT MTV layout solver."""
        centers = [list(c) for c in seed_centers]
        outer_center = [0.0, 0.0]
        
        lr_start = 0.5
        lr_end = 0.05
        pad = 1e-6 # Padding ensures verification passes safely
        
        # Precompute constants to massively speed up SAT checks
        inner_angle_rad = math.radians(inner_angle)
        v_offsets = []
        for i in range(6):
            angle = inner_angle_rad + 2 * math.pi * i / 6
            v_offsets.append((math.cos(angle) * (1.0 + pad), math.sin(angle) * (1.0 + pad)))
            
        # 3 unique positive normals for inner hexagons
        inner_normals = []
        for i in range(3):
            n_angle = inner_angle_rad + 2 * math.pi * i / 6 + math.pi / 6
            inner_normals.append((math.cos(n_angle), math.sin(n_angle)))
        min_dist = (1.0 + pad) * math.sqrt(3)
        
        # 6 inward normals for the outer hexagon
        outer_normals = []
        outer_angle_rad = math.radians(outer_angle)
        for i in range(6):
            n_out_angle = outer_angle_rad + 2 * math.pi * i / 6 + math.pi / 6
            nx = -math.cos(n_out_angle)
            ny = -math.sin(n_out_angle)
            outer_normals.append((nx, ny))
            
        # Precompute minimum projection bounds for inner vertices onto outer inward normals
        inner_proj_offsets = []
        for nx, ny in outer_normals:
            offset = min(dx * nx + dy * ny for dx, dy in v_offsets)
            inner_proj_offsets.append(offset)
            
        A = (S - pad) * math.sqrt(3) / 2.0
        
        for it in range(max_iters):
            # Cosine annealing learning rate schedule
            lr = lr_end + 0.5 * (lr_start - lr_end) * (1 + math.cos(math.pi * it / max_iters))
            max_overlap = 0.0
            
            # 1. Resolve overlaps between inner hexagons (Gauss-Seidel)
            for i in range(11):
                for j in range(i+1, 11):
                    dx = centers[j][0] - centers[i][0]
                    dy = centers[j][1] - centers[i][1]
                    
                    max_proj = -1.0
                    best_n = None
                    sign = 1.0
                    
                    # Fast SAT: check projection of center distance against the 3 normals
                    for nx, ny in inner_normals:
                        proj = dx * nx + dy * ny
                        abs_proj = abs(proj)
                        if abs_proj >= min_dist - 1e-9:
                            best_n = None
                            break # Separating axis found
                        if abs_proj > max_proj:
                            max_proj = abs_proj
                            best_n = (nx, ny)
                            sign = 1.0 if proj > 0 else -1.0
                            
                    if best_n is not None:
                        overlap = min_dist - max_proj
                        if overlap > max_overlap:
                            max_overlap = overlap
                        
                        # In-place, immediate updates applied directly to centers
                        mtv_x = -best_n[0] * sign * overlap
                        mtv_y = -best_n[1] * sign * overlap
                        
                        centers[i][0] += mtv_x * lr
                        centers[i][1] += mtv_y * lr
                        centers[j][0] -= mtv_x * lr
                        centers[j][1] -= mtv_y * lr
                        
            # 2. Resolve boundary violations with the outer hexagon
            for i in range(11):
                cx, cy = centers[i]
                total_tx = 0.0
                total_ty = 0.0
                
                for k in range(6):
                    nx, ny = outer_normals[k]
                    # Fast check: center projection + precomputed minimum vertex offset
                    min_proj = cx * nx + cy * ny + inner_proj_offsets[k]
                    if min_proj < -A:
                        depth = -A - min_proj
                        total_tx += nx * depth
                        total_ty += ny * depth
                        
                if total_tx != 0.0 or total_ty != 0.0:
                    mag = math.hypot(total_tx, total_ty)
                    if mag > max_overlap:
                        max_overlap = mag
                    centers[i][0] += total_tx * lr
                    centers[i][1] += total_ty * lr
                    
            if max_overlap < 1e-7:
                break
                
        # Final strict verification
        inner_data = [[c[0], c[1], inner_angle] for c in centers]
        if verify_construction(inner_data, outer_center, S, outer_angle):
            return centers, outer_center, True
            
        return centers, outer_center, False

    def generate_seeds():
        """Generate dense honeycomb lattice subsets."""
        seeds = []
        R = math.sqrt(3)
        base_seeds = []
        
        # 1. Standard Row-based seeds
        c_434 = []
        for i in range(4): c_434.append((i*R, 0))
        for i in range(3): c_434.append((R/2 + i*R, 1.5))
        for i in range(4): c_434.append((i*R, 3.0))
        base_seeds.append(c_434)
        
        c_344 = []
        for i in range(3): c_344.append((R/2 + i*R, 0))
        for i in range(4): c_344.append((i*R, 1.5))
        for i in range(4): c_344.append((R/2 + i*R, 3.0))
        base_seeds.append(c_344)
        
        c_443 = []
        for i in range(4): c_443.append((i*R, 0))
        for i in range(4): c_443.append((R/2 + i*R, 1.5))
        for i in range(3): c_443.append((i*R, 3.0))
        base_seeds.append(c_443)
        
        # 2. Symmetric 3-5-3 configuration
        c_353 = []
        for i in range(3): c_353.append((R + i*R, 0))
        for i in range(5): c_353.append((R/2 + i*R, 1.5))
        for i in range(3): c_353.append((R + i*R, 3.0))
        base_seeds.append(c_353)
        
        # 3. Highly symmetric 1-3-3-3-1 configuration
        c_13331 = [
            (0, 0),
            (-R/2, 1.5), (R/2, 1.5), (1.5*R, 1.5),
            (-R, 3.0), (0, 3.0), (R, 3.0),
            (-R/2, 4.5), (R/2, 4.5), (1.5*R, 4.5),
            (0, 6.0)
        ]
        base_seeds.append(c_13331)
        
        # 4. Other honeycomb variants
        c_3332 = [
            (0, 0), (R, 0), (2*R, 0),
            (R/2, 1.5), (1.5*R, 1.5), (2.5*R, 1.5),
            (0, 3.0), (R, 3.0), (2*R, 3.0),
            (R/2, 4.5), (1.5*R, 4.5)
        ]
        base_seeds.append(c_3332)
        
        c_2342 = [
            (0, 0), (R, 0),
            (-R/2, 1.5), (R/2, 1.5), (1.5*R, 1.5),
            (-R, 3.0), (0, 3.0), (R, 3.0), (2*R, 3.0),
            (-R/2, 4.5), (R/2, 4.5)
        ]
        base_seeds.append(c_2342)
        
        # 5. Compact central core seeds (1 center + 6 ring1 + 4 ring2)
        grid = []
        for row in range(-5, 6):
            for col in range(-5, 6):
                x = col * R + (R/2 if row % 2 != 0 else 0)
                y = row * 1.5
                grid.append((x, y))
        grid.sort(key=lambda p: p[0]**2 + p[1]**2)
        core = grid[:7]
        ring2 = [p for p in grid if abs(p[0]**2 + p[1]**2 - 9.0) < 0.1]
        ring2.sort(key=lambda p: math.atan2(p[1], p[0]))
        
        for i in range(6):
            subset = core + [ring2[(i+j)%6] for j in range(4)]
            base_seeds.append(subset)
            
        final_seeds = []
        for centers in base_seeds:
            # Shift to center of mass for balanced initial positioning
            cx = sum(c[0] for c in centers) / len(centers)
            cy = sum(c[1] for c in centers) / len(centers)
            centered = [(c[0]-cx, c[1]-cy) for c in centers]
            
            # Orientation 30 (pointy top)
            final_seeds.append((centered, 30.0))
            # Orientation 0 (flat top)
            flat_centers = [(-y, x) for x, y in centered]
            final_seeds.append((flat_centers, 0.0))
            
        return final_seeds

    # Generate an extensive suite of geometric seeds
    seeds = generate_seeds()
    # Finer angle sweep taking advantage of the massive speedup
    outer_angles = [float(a) for a in range(0, 60, 2)]
    
    best_S = 4.2
    best_config = None
    
    # 1. Coarse Search to identify promising basins of attraction
    for seed_centers, inner_angle in seeds:
        for outer_angle in outer_angles:
            low = 3.7
            high = best_S
            
            # Quick check to prune unpromising configurations
            current_centers, current_oc, success = optimize_layout(
                seed_centers, inner_angle, outer_angle, high, max_iters=1500)
            
            if not success:
                continue
                
            valid_centers = current_centers
            valid_oc = current_oc
            
            # Extended Binary search (25 steps) to find the minimum feasible side length S
            for step in range(25):
                mid = (low + high) / 2.0
                test_centers, test_oc, success = optimize_layout(
                    current_centers, inner_angle, outer_angle, mid, max_iters=1500)
                
                if success:
                    high = mid
                    valid_centers = test_centers
                    valid_oc = test_oc
                    # Warm start for the next iteration
                    current_centers = test_centers
                else:
                    low = mid
                    
            # Update best configuration found so far
            if high < best_S:
                best_S = high
                best_config = (valid_centers, inner_angle, outer_angle, high, valid_oc, seed_centers)
                
    # 2. Fine-grained Micro-Sweep around the best outer angle
    if best_config is not None:
        _, best_inner_angle, best_outer_angle, _, _, best_seed_centers = best_config
        micro_angles = [best_outer_angle + d * 0.25 for d in range(-8, 9) if d != 0]
        
        for outer_angle in micro_angles:
            low = 3.7
            high = best_S
            
            current_centers, current_oc, success = optimize_layout(
                best_seed_centers, best_inner_angle, outer_angle, high, max_iters=1500)
            
            if not success:
                continue
                
            valid_centers = current_centers
            valid_oc = current_oc
            
            for step in range(25):
                mid = (low + high) / 2.0
                test_centers, test_oc, success = optimize_layout(
                    current_centers, best_inner_angle, outer_angle, mid, max_iters=1500)
                
                if success:
                    high = mid
                    valid_centers = test_centers
                    valid_oc = test_oc
                    current_centers = test_centers
                else:
                    low = mid
                    
            if high < best_S:
                best_S = high
                best_config = (valid_centers, best_inner_angle, outer_angle, high, valid_oc, best_seed_centers)
                
    if best_config is None:
        # Fallback (should theoretically not be reached with the extensive seed set)
        seed_centers, inner_angle = seeds[0]
        return [[c[0]+10, c[1]+10, inner_angle] for c in seed_centers], [10.0, 10.0], 4.2, 0.0
        
    centers, inner_angle, outer_angle, S, oc, _ = best_config
    
    # Normalize coordinates to ensure x > 0 and y > 0
    min_x = min(c[0] for c in centers) - S
    min_y = min(c[1] for c in centers) - S
    
    shift_x = -min_x + 1.0
    shift_y = -min_y + 1.0
    
    final_inner = [[c[0] + shift_x, c[1] + shift_y, inner_angle] for c in centers]
    final_oc = [oc[0] + shift_x, oc[1] + shift_y]
    
    return final_inner, final_oc, S, outer_angle
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
