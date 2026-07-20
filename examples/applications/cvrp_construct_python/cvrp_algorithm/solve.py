#!/usr/bin/env python3
"""CVRP greedy route-construction solver with EVOLVE markers for LLM4AD.

Migrated from the legacy LLM4AD ``cvrp_construct`` task
(https://github.com/Optima-CityU/LLM4AD, llm4ad/task/optimization/cvrp_construct).

The evolvable heuristic ``select_next_node`` is repeatedly called by the
route-construction driver to build a complete capacitated vehicle routing
solution. The driver reads a JSON instance file (path passed as argv[1]) and
prints the resulting route and total cost as JSON on stdout.
"""

import copy
import json
import sys

import numpy as np


# EVOLVE_START
def select_next_node(
    current_node: int,
    depot: int,
    unvisited_nodes: np.ndarray,
    rest_capacity: float,
    demands: np.ndarray,
    distance_matrix: np.ndarray,
) -> int:
    """Design a novel algorithm to select the next node in each step.

    Args:
        current_node: ID of the current node.
        depot: ID of the depot.
        unvisited_nodes: Array of IDs of feasible unvisited nodes.
        rest_capacity: Remaining capacity of the vehicle.
        demands: Demands of all nodes.
        distance_matrix: Distance matrix of nodes.

    Returns:
        ID of the next node to visit (return the depot ID to close the
        current vehicle trip and start a new one).
    """
    best_score = -1.0
    next_node = -1

    for node in unvisited_nodes:
        demand = demands[node]
        distance = distance_matrix[current_node][node]

        if demand <= rest_capacity:
            # Avoid division by zero
            score = demand / distance if distance > 0 else float("inf")
            if score > best_score:
                best_score = score
                next_node = node

    return next_node
# EVOLVE_END


def tour_cost(coordinates: np.ndarray, solution: list[int]) -> float:
    """Compute total Euclidean distance of the route, closing back to the start.

    Args:
        coordinates: Node coordinates, shape (n_nodes, 2).
        solution: Ordered list of node indices forming the full route.

    Returns:
        Total Euclidean distance of the route.
    """
    cost = 0.0
    for j in range(len(solution) - 1):
        cost += np.linalg.norm(
            coordinates[int(solution[j])] - coordinates[int(solution[j + 1])]
        )
    cost += np.linalg.norm(
        coordinates[int(solution[-1])] - coordinates[int(solution[0])]
    )
    return float(cost)


def route_construct(
    distance_matrix: np.ndarray,
    demands: np.ndarray,
    vehicle_capacity: int,
    problem_size: int,
) -> list[int] | None:
    """Build a complete CVRP route using the evolved ``select_next_node``.

    Faithfully ports the legacy driver logic: node 0 is the depot, the vehicle
    greedily selects the next node until capacity forces a return, and the
    route is invalid if not all customers are served.

    Args:
        distance_matrix: Pairwise distance matrix, shape (n, n).
        demands: Demand per node (depot demand is 0).
        vehicle_capacity: Maximum capacity per vehicle.
        problem_size: Total number of nodes including the depot.

    Returns:
        The full route as a list of node indices, or ``None`` if the
        constructed route fails to visit every node.
    """
    route: list[int] = []
    current_load = 0
    current_node = 0
    route.append(current_node)

    unvisited_nodes = set(range(1, problem_size))  # node 0 is the depot
    all_nodes = np.array(list(unvisited_nodes))
    feasible_unvisited_nodes = all_nodes

    while unvisited_nodes:
        next_node = select_next_node(
            current_node,
            0,
            feasible_unvisited_nodes,
            vehicle_capacity - current_load,
            copy.deepcopy(demands),
            copy.deepcopy(distance_matrix),
        )

        if next_node == 0:
            route.append(next_node)
            current_load = 0
            current_node = 0
        else:
            route.append(next_node)
            current_load += demands[next_node]
            unvisited_nodes.remove(next_node)
            current_node = next_node

        feasible_nodes_capacity = np.array(
            [
                node
                for node in all_nodes
                if current_load + demands[node] <= vehicle_capacity
            ]
        )
        feasible_unvisited_nodes = np.intersect1d(
            feasible_nodes_capacity, list(unvisited_nodes)
        )

        if len(unvisited_nodes) > 0 and len(feasible_unvisited_nodes) < 1:
            route.append(0)
            current_load = 0
            current_node = 0
            feasible_unvisited_nodes = np.array(list(unvisited_nodes))

    # Validate that every node has been visited
    if len(set(route)) != problem_size:
        return None
    # Cast to native ints (route may contain numpy integers from intersect1d)
    return [int(node) for node in route]


def solve(input_data: dict) -> dict:
    """Solve a CVRP instance using the greedy route-construction heuristic.

    Args:
        input_data: Dict with keys ``coordinates``, ``distances``,
            ``demands``, ``capacity`` (and optionally ``label``).

    Returns:
        Dict with ``routes`` (list of node indices) and ``total_cost`` (float).
        On an invalid construction, returns an empty route and infinite cost.
    """
    coordinates = np.array(input_data["coordinates"], dtype=float)
    distance_matrix = np.array(input_data["distances"], dtype=float)
    demands = np.array(input_data["demands"], dtype=float)
    vehicle_capacity = int(input_data["capacity"])
    problem_size = len(demands)

    route = route_construct(distance_matrix, demands, vehicle_capacity, problem_size)

    if route is None:
        return {"routes": [], "total_cost": float("inf")}

    total_cost = tour_cost(coordinates, route)
    return {"routes": route, "total_cost": float(total_cost)}


def main():
    """Entry point: read an instance JSON file and print the solution JSON."""
    if len(sys.argv) < 2:
        print("Usage: python solve.py <input.json>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        input_data = json.load(f)

    result = solve(input_data)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
