#!/usr/bin/env python3
"""Generate CVRP instances as JSON files for the LLM4AD_Next evaluator.

Migrated from the legacy LLM4AD ``cvrp_construct/get_instance.py``. Each
instance keeps the same generation scheme (uniform coordinates in the unit
square, integer demands in [1, 10)), scaled here to a 0-100 coordinate range
for readability. A baseline ``label`` is computed with a simple
demand-over-distance greedy heuristic so the evaluator's normalized gap is
meaningful.

Usage:
    python generate_data.py                 # default 16 instances, 50 customers
    python generate_data.py --n 8 --size 20 --out data/small
"""

import argparse
import copy
import json
from pathlib import Path

import numpy as np


def _baseline_select_next_node(current_node, unvisited_nodes, rest_capacity, demands, distance_matrix):
    """Reference greedy heuristic used to compute a per-instance baseline.

    Mirrors the legacy default ``select_next_node`` (highest demand-to-distance
    ratio among capacity-feasible unvisited nodes).
    """
    best_score = -1.0
    next_node = -1
    for node in unvisited_nodes:
        demand = demands[node]
        distance = distance_matrix[current_node][node]
        if demand <= rest_capacity:
            score = demand / distance if distance > 0 else float("inf")
            if score > best_score:
                best_score = score
                next_node = node
    return next_node


def _baseline_cost(coordinates, distance_matrix, demands, capacity, problem_size):
    """Construct a route with the baseline heuristic and return its cost."""
    route = [0]
    current_load = 0
    current_node = 0
    unvisited = set(range(1, problem_size))
    all_nodes = np.array(list(unvisited))
    feasible = all_nodes

    while unvisited:
        nxt = _baseline_select_next_node(
            current_node, feasible, capacity - current_load,
            copy.deepcopy(demands), copy.deepcopy(distance_matrix),
        )
        if nxt == 0:
            route.append(0)
            current_load = 0
            current_node = 0
        else:
            route.append(nxt)
            current_load += demands[nxt]
            unvisited.remove(nxt)
            current_node = nxt

        feasible_cap = np.array(
            [n for n in all_nodes if current_load + demands[n] <= capacity]
        )
        feasible = np.intersect1d(feasible_cap, list(unvisited))
        if len(unvisited) > 0 and len(feasible) < 1:
            route.append(0)
            current_load = 0
            current_node = 0
            feasible = np.array(list(unvisited))

    if len(set(route)) != problem_size:
        return None

    cost = 0.0
    for j in range(len(route) - 1):
        cost += np.linalg.norm(coordinates[route[j]] - coordinates[route[j + 1]])
    cost += np.linalg.norm(coordinates[route[-1]] - coordinates[route[0]])
    return float(cost)


def generate_instances(n_instance: int, n_customers: int, capacity: int, seed: int = 2024):
    """Generate CVRP instances.

    Args:
        n_instance: Number of instances to generate.
        n_customers: Number of customers (the depot is added on top).
        capacity: Vehicle capacity.
        seed: Random seed for reproducibility (legacy used 2024).

    Returns:
        List of instance dicts with coordinates/distances/demands/capacity/label.
    """
    rng = np.random.RandomState(seed)
    problem_size = n_customers + 1  # include depot
    instances = []

    for _ in range(n_instance):
        coordinates = rng.rand(problem_size, 2) * 100.0
        demands = rng.randint(1, 10, size=problem_size).astype(float)
        demands[0] = 0.0  # depot has no demand
        distances = np.linalg.norm(
            coordinates[:, np.newaxis] - coordinates, axis=2
        )

        baseline = _baseline_cost(
            coordinates, distances, demands, capacity, problem_size
        )

        instances.append(
            {
                "coordinates": np.round(coordinates, 4).tolist(),
                "distances": np.round(distances, 4).tolist(),
                "demands": [int(d) for d in demands],
                "capacity": int(capacity),
                "label": round(baseline, 4) if baseline is not None else 0.0,
            }
        )

    return instances


def main():
    """Generate instances and write them as individual JSON files."""
    parser = argparse.ArgumentParser(description="Generate CVRP instances")
    parser.add_argument("--n", type=int, default=16, help="Number of instances")
    parser.add_argument("--size", type=int, default=50, help="Number of customers")
    parser.add_argument("--capacity", type=int, default=40, help="Vehicle capacity")
    parser.add_argument("--seed", type=int, default=2024, help="Random seed")
    parser.add_argument(
        "--out",
        type=str,
        default="data/instances",
        help="Output directory (relative to this script)",
    )
    args = parser.parse_args()

    out_dir = Path(__file__).parent / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    instances = generate_instances(args.n, args.size, args.capacity, args.seed)
    for i, inst in enumerate(instances):
        out_path = out_dir / f"instance_{i:03d}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(inst, f)

    print(f"Wrote {len(instances)} instances to {out_dir}")


if __name__ == "__main__":
    main()
