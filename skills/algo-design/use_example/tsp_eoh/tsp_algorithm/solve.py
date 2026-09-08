#!/usr/bin/env python3
"""TSP solver with EVOLVE markers for LLM4AD."""

import json
import sys
import numpy as np


# EVOLVE_START
def nearest_neighbor_tsp(nodes):
    n = len(nodes)
    if n <= 1:
        return list(range(n))
    
    nodes_array = np.array(nodes, dtype=np.float64)
    unvisited = set(range(1, n))
    tour = [0]
    current = 0

    while unvisited:
        current_pos = nodes_array[current]
        best_dist = float("inf")
        best_next = -1

        for idx in unvisited:
            dist = np.linalg.norm(nodes_array[idx] - current_pos)
            if dist < best_dist:
                best_dist = dist
                best_next = idx

        tour.append(best_next)
        unvisited.remove(best_next)
        current = best_next

    return tour
# EVOLVE_END


def calculate_tour_length(nodes, tour):
    if len(tour) < 2:
        return 0.0
    total = 0.0
    nodes = np.array(nodes)
    for i in range(len(tour) - 1):
        total += np.linalg.norm(nodes[tour[i]] - nodes[tour[i + 1]])
    total += np.linalg.norm(nodes[tour[-1]] - nodes[tour[0]])
    return total


def solve(nodes):
    tour = nearest_neighbor_tsp(nodes)
    tour_length = calculate_tour_length(nodes, tour)
    return {"tour": tour, "tour_length": tour_length}


def main():
    if len(sys.argv) < 2:
        sys.exit(1)
    input_data = json.loads(sys.argv[1])
    nodes = input_data.get("nodes", [])
    result = solve(nodes)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
