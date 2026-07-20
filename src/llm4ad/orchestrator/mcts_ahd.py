"""MCTS-AHD orchestrator: Monte Carlo Tree Search for automatic heuristic design.

Migrated from legacy LLM4AD MCTS-AHD method (llm4ad/method/mcts_ahd/). Instead
of a generational population, MCTS-AHD organizes candidate algorithms as a tree
and uses UCT to balance exploration and exploitation.

Design choice: this orchestrator **inherits** ``IslandGAOrchestrator`` to reuse
its verified ``generate_new_individual`` pipeline (worktree -> plan -> implement
-> build -> commit -> evaluate) and its checkpoint/status helpers. Only the
search control flow (``run``/``step``/``initialize_population``/
``evolve_generation``) is overridden with tree-search semantics.

The tree structure (``MCTS``/``MCTSNode``) lives in ``mcts_ahd_tree``.
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from llm4ad.infra.state import EvolutionState
from llm4ad.orchestrator.base import BaseOrchestrator, EvolutionResult
from llm4ad.orchestrator.island_ga import IslandGAOrchestrator
from llm4ad.orchestrator.mcts_ahd_tree import MCTS, MCTSNode
from llm4ad.planner.base import Algorithm


@BaseOrchestrator.register("mcts_ahd")
class MCTSAHDOrchestrator(IslandGAOrchestrator):
    """MCTS-AHD orchestrator using UCT-guided tree search.

    Reuses the IslandGA individual-generation pipeline but replaces the
    generational loop with MCTS selection/expansion/backpropagation.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the MCTS-AHD orchestrator and its search tree."""
        super().__init__(*args, **kwargs)
        # MCTS parameters (read from config with legacy defaults).
        self._lambda0 = float(self._cfg("lambda0", 0.1))
        self._alpha = float(self._cfg("alpha", 0.5))
        self._max_depth = int(self._cfg("max_depth", 10))
        self._init_size = int(self._cfg("init_size", 4))
        self._max_sample_nums = int(self._cfg("max_sample_nums", 100))

        self.mcts = MCTS(
            root_algorithm=None,
            lambda0=self._lambda0,
            alpha=self._alpha,
            max_depth=self._max_depth,
        )
        self._sample_count = 0

    def _cfg(self, key: str, default: Any) -> Any:
        """Read a config value (works for dict or pydantic config)."""
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    async def initialize_population(self) -> list[Algorithm]:
        """Create the initial tree children under the root.

        Generates ``init_size`` individuals as first-level children of the
        root node.

        Returns:
            The initial population of evaluated algorithms.
        """
        logger.info(f"[MCTS-AHD] Initializing {self._init_size} root children")
        population: list[Algorithm] = []
        for _ in range(self._init_size):
            algorithm = await self.generate_new_individual(
                parents=[], island_id=0, generation=0
            )
            if algorithm is None or not algorithm.is_evaluated():
                continue
            node = MCTSNode(algorithm=algorithm, depth=1, parent=self.mcts.root)
            node.Q = algorithm.score
            node.reward = algorithm.score
            self.mcts.root.add_child(node)
            self.mcts.backpropagate(node)
            population.append(algorithm)
            self._sample_count += 1
            self._track_best(algorithm)
        self.population = population
        return population

    def _select_node(self) -> MCTSNode:
        """Select a node to expand by descending UCT from the root.

        Returns:
            The selected node (root if the tree only has the root).
        """
        eval_remain = max(0.0, 1.0 - self._sample_count / max(1, self._max_sample_nums))
        node = self.mcts.root
        while node.children and node.depth < self._max_depth:
            node = max(
                node.children, key=lambda child: self.mcts.uct(child, eval_remain)
            )
        return node

    async def evolve_generation(self, parent_population: list[Algorithm]) -> list[Algorithm]:
        """Run one MCTS expansion step.

        Selects a node via UCT, expands it by generating a child algorithm from
        the selected node's algorithm as parent, evaluates it, and
        backpropagates the reward.

        Args:
            parent_population: Current population (unused directly; the tree
                holds parent state).

        Returns:
            The updated population including the new child, if any.
        """
        node = self._select_node()
        parents = [node.algorithm] if node.algorithm is not None else []

        child_algorithm = await self.generate_new_individual(
            parents=parents, island_id=0, generation=self.current_generation
        )
        if child_algorithm is None or not child_algorithm.is_evaluated():
            return self.population

        child_node = MCTSNode(
            algorithm=child_algorithm,
            depth=node.depth + 1,
            parent=node,
        )
        child_node.Q = child_algorithm.score
        child_node.reward = child_algorithm.score
        node.add_child(child_node)
        self.mcts.backpropagate(child_node)

        self.population.append(child_algorithm)
        self._sample_count += 1
        self._track_best(child_algorithm)
        return self.population

    def _track_best(self, algorithm: Algorithm) -> None:
        """Update the global best individual."""
        if algorithm is None or not algorithm.is_evaluated():
            return
        if self.global_best_individual is None or algorithm.score > self.global_best_individual.score:
            self.global_best_individual = algorithm
            self.best_individual = algorithm

    async def step(self) -> tuple[bool, Algorithm | None]:
        """Execute one MCTS step (one expansion).

        Returns:
            Tuple of (should_continue, current_best_individual).
        """
        await self.evolve_generation(self.population)
        self.current_generation += 1
        should_continue = self._sample_count < self._max_sample_nums
        self.history.append(
            {
                "generation": self.current_generation,
                "samples": self._sample_count,
                "best_score": self.global_best_individual.score
                if self.global_best_individual
                else 0.0,
            }
        )
        return should_continue, self.global_best_individual

    async def run(self) -> EvolutionResult:
        """Run the full MCTS-AHD search loop.

        Returns:
            EvolutionResult with the best individual and final tree population.
        """
        self.state = EvolutionState.RUNNING
        self.start_time = time.time()
        logger.info(
            f"[MCTS-AHD] Starting search: max_samples={self._max_sample_nums}, "
            f"init_size={self._init_size}, lambda0={self._lambda0}"
        )

        await self.initialize_population()

        while self._sample_count < self._max_sample_nums:
            should_continue, _ = await self.step()
            if not should_continue:
                break
            if self.should_checkpoint():
                await self.save_checkpoint()
                self._last_checkpoint_gen = self.current_generation

        await self._finish_embedding_tasks()
        self.state = EvolutionState.COMPLETED
        duration = time.time() - self.start_time
        logger.info(
            f"[MCTS-AHD] Search complete: {self._sample_count} samples, "
            f"best score={self.global_best_individual.score if self.global_best_individual else 0.0:.4f}"
        )

        return EvolutionResult(
            state=self.state,
            best_individual=self.global_best_individual,
            final_population=self.population,
            final_generation=self.current_generation,
            total_evaluations=self._sample_count,
            history=self.history,
            duration_seconds=duration,
        )
