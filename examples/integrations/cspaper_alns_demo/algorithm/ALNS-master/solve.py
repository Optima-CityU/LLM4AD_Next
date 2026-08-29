"""TSP solver using ALNS with an evolvable destroy operator."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import numpy as np
import tsplib95

from alns import ALNS
from alns.accept import HillClimbing
from alns.select import RouletteWheel
from alns.stop import MaxIterations


class TspState:
    """A complete or partially destroyed TSP solution."""

    def __init__(
        self,
        nodes: list[int],
        edges: dict[int, int],
        distances: dict[tuple[int, int], float],
    ) -> None:
        self.nodes = nodes
        self.edges = edges
        self.distances = distances

    def objective(self) -> float:
        """Return total tour length for a complete solution."""
        if len(self.edges) != len(self.nodes):
            raise ValueError("Cannot evaluate an incomplete TSP solution.")

        return sum(
            self.distances[node, self.edges[node]]
            for node in self.nodes
        )

    def to_tour(self) -> list[int]:
        """Convert the edge representation into an ordered tour."""
        if len(self.edges) != len(self.nodes):
            raise ValueError("Cannot export an incomplete TSP solution.")

        start = self.nodes[0]
        tour = [start]
        current = start

        for _ in range(len(self.nodes) - 1):
            current = self.edges[current]

            if current in tour:
                raise ValueError("Solution contains a subcycle.")

            tour.append(current)

        if self.edges[current] != start:
            raise ValueError("Solution does not return to its start.")

        if set(tour) != set(self.nodes):
            raise ValueError("Solution does not visit every node.")

        return tour


def load_instance(
    instance_path: str | Path,
) -> tuple[list[int], dict[tuple[int, int], float]]:
    """Load a TSPLIB instance and precompute all edge distances."""
    problem = tsplib95.load(str(Path(instance_path).resolve()))
    nodes = list(problem.get_nodes())

    if len(nodes) < 3:
        raise ValueError("A TSP instance must contain at least three nodes.")

    distances = {
        (node_from, node_to): float(
            problem.get_weight(node_from, node_to)
        )
        for node_from in nodes
        for node_to in nodes
    }

    return nodes, distances


def would_form_subcycle(
    from_node: int,
    to_node: int,
    state: TspState,
) -> bool:
    """Return whether adding an edge would create a premature cycle."""
    for step in range(1, len(state.nodes)):
        if to_node not in state.edges:
            return False

        to_node = state.edges[to_node]

        if from_node == to_node:
            return step != len(state.nodes) - 1

    return False


def repair_operator(
    current: TspState,
    rng: np.random.Generator,
    **_: Any,
) -> TspState:
    """Repair a partial tour using randomized greedy insertion."""
    visited = set(current.edges.values())
    shuffled_indices = rng.permutation(len(current.nodes))
    ordered_nodes = [
        current.nodes[index]
        for index in shuffled_indices
    ]

    while len(current.edges) < len(current.nodes):
        node = next(
            candidate
            for candidate in ordered_nodes
            if candidate not in current.edges
        )

        feasible_nodes = [
            other
            for other in current.nodes
            if other != node
            and other not in visited
            and not would_form_subcycle(node, other, current)
        ]

        if not feasible_nodes:
            raise ValueError("Repair operator cannot complete the tour.")

        nearest = min(
            feasible_nodes,
            key=lambda other: current.distances[node, other],
        )

        current.edges[node] = nearest
        visited.add(nearest)

    return current


def number_of_edges_to_remove(
    state: TspState,
    degree_of_destruction: float,
) -> int:
    """Calculate a bounded number of edges to remove."""
    if not 0 < degree_of_destruction < 1:
        raise ValueError(
            "degree_of_destruction must be between zero and one."
        )

    requested = round(
        len(state.nodes) * degree_of_destruction
    )

    return max(
        1,
        min(int(requested), len(state.nodes) - 1),
    )


# EVOLVE_START 
def destroy_operator(
    current: TspState,
    rng: np.random.Generator,
    degree_of_destruction: float = 0.1,
    **_: Any,
) -> TspState:
    """Destroy a tour by removing randomly selected outgoing edges."""
    destroyed = copy.deepcopy(current)

    remove_count = number_of_edges_to_remove(
        destroyed,
        degree_of_destruction,
    )

    departures = list(destroyed.edges)
    selected_indices = rng.choice(
        len(departures),
        size=remove_count,
        replace=False,
    )

    for index in selected_indices:
        del destroyed.edges[departures[int(index)]]

    return destroyed
# EVOLVE_END


def solve(
    instance_path: str | Path,
    *,
    seed: int = 42,
    max_iterations: int = 1_000,
    degree_of_destruction: float = 0.1,
) -> dict[str, Any]:
    """Solve one TSPLIB instance and return an auditable result."""
    if max_iterations < 1:
        raise ValueError("max_iterations must be positive.")

    nodes, distances = load_instance(instance_path)
    rng = np.random.default_rng(seed)

    empty_state = TspState(
        nodes=nodes,
        edges={},
        distances=distances,
    )
    initial_solution = repair_operator(empty_state, rng)

    search = ALNS(rng)
    search.add_destroy_operator(
        destroy_operator,
        name="evolvable_destroy",
    )
    search.add_repair_operator(
        repair_operator,
        name="fixed_greedy_repair",
    )

    selector = RouletteWheel(
        scores=[5, 3, 1, 0],
        decay=0.8,
        num_destroy=1,
        num_repair=1,
    )
    acceptance = HillClimbing()
    stopping = MaxIterations(max_iterations)

    result = search.iterate(
        initial_solution,
        selector,
        acceptance,
        stopping,
        degree_of_destruction=degree_of_destruction,
    )

    best = result.best_state

    return {
        "tour": best.to_tour(),
        "objective": best.objective(),
        "seed": seed,
        "max_iterations": max_iterations,
        "degree_of_destruction": degree_of_destruction,
    }


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("instance", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=1_000,
    )
    parser.add_argument(
        "--degree-of-destruction",
        type=float,
        default=0.1,
    )
    args = parser.parse_args()

    result = solve(
        args.instance,
        seed=args.seed,
        max_iterations=args.max_iterations,
        degree_of_destruction=args.degree_of_destruction,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()