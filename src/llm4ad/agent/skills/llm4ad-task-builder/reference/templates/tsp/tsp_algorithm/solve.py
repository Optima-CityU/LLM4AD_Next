"""TSP solver using a nearest-neighbor heuristic.

This script demonstrates the one-shot solver pattern:
- Reads problem instance from sys.argv[1] as JSON
- Computes a solution
- Prints result as JSON to stdout

The function between EVOLVE_START and EVOLVE_END will be evolved by the LLM.
"""

import json
import sys


# EVOLVE_START
def nearest_neighbor_tsp(nodes: list[list[float]]) -> list[int]:
    """Solve TSP using nearest neighbor heuristic.

    Args:
        nodes: List of [x, y] coordinates for each city.

    Returns:
        Tour as a list of node indices (permutation of range(len(nodes))).
    """
    n = len(nodes)
    if n == 0:
        return []
    if n == 1:
        return [0]

    # Start from node 0
    tour = [0]
    unvisited = set(range(1, n))

    current = 0
    while unvisited:
        # Find nearest unvisited node
        nearest = min(
            unvisited,
            key=lambda node: (
                (nodes[node][0] - nodes[current][0]) ** 2
                + (nodes[node][1] - nodes[current][1]) ** 2
            )
            ** 0.5,
        )
        tour.append(nearest)
        unvisited.remove(nearest)
        current = nearest

    return tour
# EVOLVE_END


def solve(problem_data: dict) -> dict:
    """Solve the TSP instance and return the result.

    Args:
        problem_data: Dictionary with "nodes" key containing list of [x, y] coordinates.

    Returns:
        Dictionary with "tour" (list of node indices) and "tour_length" (float).
    """
    nodes = problem_data["nodes"]
    tour = nearest_neighbor_tsp(nodes)

    # Calculate tour length
    tour_length = 0.0
    for i in range(len(tour)):
        from_node = nodes[tour[i]]
        to_node = nodes[tour[(i + 1) % len(tour)]]
        dx = to_node[0] - from_node[0]
        dy = to_node[1] - from_node[1]
        tour_length += (dx**2 + dy**2) ** 0.5

    return {"tour": tour, "tour_length": tour_length}


def main():
    """Main entry point: read JSON from argv[1], solve, print JSON result."""
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No input provided"}))
        sys.exit(1)

    try:
        input_data = json.loads(sys.argv[1])
        result = solve(input_data)
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
