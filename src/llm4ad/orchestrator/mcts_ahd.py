"""MCTS-AHD orchestrator implementation.

Migrated from the legacy LLM4AD ``method/mcts_ahd``. Builds a Monte Carlo
search tree over algorithms:

1. Initialize: generate one root algorithm (i1), then expand the root into
   ``init_size`` children via e1.
2. Search loop (each iteration = one "generation"):
   - Walk down from the root selecting children by UCT until a leaf.
   - Expand the selected node with the e2/m1/m2/s1 operators (weighted).
   - Evaluate offspring, register into the active pool, and backpropagate.

Score convention: higher is better (consistent with the rest of the
platform). ``max_generations`` is the number of MCTS iterations.

Reference:
    Zheng et al. "Monte Carlo Tree Search for Comprehensive Exploration in
    LLM-based Automatic Heuristic Design." ICML 2025.
"""

from __future__ import annotations

import random
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from llm4ad.coder.base import BaseCoder
from llm4ad.config.evolution import MCTSAHDConfig
from llm4ad.evaluator import EvaluationDispatcher, EvaluationResult
from llm4ad.infra.state import EvolutionState, GenerationMetrics, StateTracker
from llm4ad.infra.timing import ExecutionTiming
from llm4ad.infra.version_control.base import BaseVersionControl
from llm4ad.orchestrator.base import BaseOrchestrator, EvolutionCheckpoint, EvolutionResult, format_duration_ms
from llm4ad.orchestrator.embedding_client import EmbeddingClient
from llm4ad.orchestrator.mcts_tree import MCTS, MCTSNode
from llm4ad.planner.base import Algorithm, BasePlanner
from llm4ad.planner.mcts_ahd_evolution import MCTSAHDEvolutionPlanner


def _code_signature(algorithm: Algorithm) -> str:
    """Concatenate an algorithm's code artifacts for duplicate detection.

    Args:
        algorithm: Algorithm whose artifacts are joined.

    Returns:
        Concatenated artifact contents.
    """
    return "\n".join(a.content for a in algorithm.code_artifacts)


@BaseOrchestrator.register("mcts_ahd")
class MCTSAHDOrchestrator(BaseOrchestrator):
    """MCTS-based Automatic Heuristic Design orchestrator."""

    # Expansion operators applied at the selected node, with repeat weights.
    _OP_WEIGHTS = [("e2", 1), ("m1", 2), ("m2", 2), ("s1", 1)]

    def __init__(
        self,
        planner: BasePlanner,
        coder: BaseCoder,
        dispatcher: EvaluationDispatcher,
        monitor: Any,
        config: MCTSAHDConfig,
        version_control: BaseVersionControl,
        state_tracker: StateTracker,
        background: str = "",
        embedding_client: EmbeddingClient = None,
    ) -> None:
        """Initialize the MCTS-AHD orchestrator.

        Args:
            planner: Must be a ``MCTSAHDEvolutionPlanner``.
            coder: Coder used by the planner's ``implement``.
            dispatcher: Evaluation dispatcher.
            monitor: Progress monitor.
            config: MCTS-AHD configuration.
            version_control: Worktree manager.
            state_tracker: Run state / timing tracker.
            background: Problem background passed to samplers.
            embedding_client: Optional embedding client.
        """
        super().__init__(planner, coder, dispatcher, monitor, config, state_tracker, background, embedding_client)
        if not isinstance(planner, MCTSAHDEvolutionPlanner):
            raise TypeError("MCTSAHDOrchestrator requires a MCTSAHDEvolutionPlanner")
        self.planner: MCTSAHDEvolutionPlanner = planner
        self.config: MCTSAHDConfig = config
        self.version_control = version_control
        self._background = background or getattr(config, "background", "")
        self.total_samples = 0
        self._iteration = 0

        self.mcts = MCTS("Root", config.alpha, config.lambda_0, max_depth=config.max_depth)
        self.pool: list[Algorithm] = []  # active algorithm pool (for e2 selection)

        self._eval_successes = 0
        self._eval_failures = 0
        self._operator_stats: dict[str, dict[str, int]] = {}

    @property  # type: ignore[override]
    def current_generation(self) -> int:  # type: ignore[override]
        """Expose the MCTS iteration count as the generation."""
        return self._iteration

    @current_generation.setter
    def current_generation(self, value: int) -> None:
        """Ignore base-class assignments (iteration owned by the search loop)."""
        pass

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #

    async def run(self) -> EvolutionResult:
        """Run the MCTS-AHD search until a termination condition is met.

        Returns:
            EvolutionResult with the best individual and final pool.
        """
        self.state = EvolutionState.RUNNING
        self.start_time = time.time()
        self.state_tracker.start_run(self.config.max_generations)
        try:
            if not self.pool:
                await self.initialize_population()
                self._log_generation_stats()
            while self.state == EvolutionState.RUNNING:
                if self._iteration >= self.config.max_generations:
                    break
                if self.total_samples >= self.config.max_sample_nums:
                    break
                should_continue, best = await self.step()
                if not should_continue:
                    break
                self.best_individual = best
            self.state = EvolutionState.COMPLETED
        except Exception:
            self.state = EvolutionState.FAILED
            raise
        finally:
            self._log_timing_summary()
            self.state_tracker.end_run(self.state)

        best = self._best_in_pool()
        self.best_individual = best
        return EvolutionResult(
            state=self.state,
            best_individual=best,
            final_population=list(self.pool),
            final_generation=self._iteration,
            total_evaluations=self.total_samples,
            history=self.history,
            duration_seconds=time.time() - self.start_time,
            metadata={
                "pool_size": len(self.pool),
                "tree_nodes": self._count_nodes(),
                "best_score": best.score if best else None,
            },
        )

    async def initialize_population(self) -> list[Algorithm]:
        """Seed the root with one i1 algorithm, then expand into init_size children.

        Returns:
            The initial active pool.
        """
        init_start = time.time()

        # 1. One root algorithm via i1.
        root_algo = None
        attempts = 0
        while root_algo is None and self.total_samples < self.config.max_sample_nums and attempts < 3:
            attempts += 1
            cand = await self._generate([], "i1")
            self.total_samples += 1
            if cand is not None and cand.is_evaluated():
                root_algo = cand

        if root_algo is None:
            logger.warning("[MCTS-AHD] Failed to generate a valid root algorithm")
            self.state_tracker.record_timing("init_population", (time.time() - init_start) * 1000)
            return []

        self._add_node(root_algo, parent=self.mcts.root)
        self.pool.append(root_algo)

        # 2. Expand the root into init_size children via e1.
        init_target = self.config.init_size
        while (
            len(self.mcts.root.children) < init_target
            and self.total_samples < self.config.max_sample_nums
        ):
            parents = self._select_pool(min(self.config.selection_num, len(self.pool)))
            cand = await self._generate(parents, "e1")
            self.total_samples += 1
            if cand is None or not cand.is_evaluated():
                continue
            if self._is_duplicate(cand):
                continue
            self._add_node(cand, parent=self.mcts.root)
            self.pool.append(cand)
            self._truncate_pool()

        self.state_tracker.record_timing("init_population", (time.time() - init_start) * 1000)
        self.population = list(self.pool)
        self.best_individual = self._best_in_pool()
        logger.info(
            f"[MCTS-AHD] Initialized: root + {len(self.mcts.root.children)} children, "
            f"pool={len(self.pool)}, samples={self.total_samples}"
        )
        return self.population

    async def step(self) -> tuple[bool, Algorithm | None]:
        """One MCTS iteration: select via UCT, expand with operators, backprop.

        Returns:
            Tuple of (should_continue, current best individual).
        """
        if self.state != EvolutionState.RUNNING:
            return False, self._best_in_pool()
        if not self.mcts.root.children:
            return False, self._best_in_pool()

        # 1. Selection: walk down by UCT to a node to expand.
        eval_remain = max(1 - self.total_samples / self.config.max_sample_nums, 0.0)
        cur = self.mcts.root
        while cur.children and cur.depth < self.mcts.max_depth:
            cur = max(cur.children, key=lambda n: self.mcts.uct(n, eval_remain))

        # 2. Expansion: apply the weighted operators at the selected node.
        for operator, weight in self._OP_WEIGHTS:
            for _ in range(weight):
                if self.total_samples >= self.config.max_sample_nums:
                    break
                parents = self._select_parents_for_operator(operator, cur)
                if not parents:
                    continue
                cand = await self._generate(parents, operator)
                self.total_samples += 1
                if cand is None or not cand.is_evaluated():
                    continue
                if self._is_duplicate(cand):
                    continue
                node = self._add_node(cand, parent=cur)
                self.pool.append(cand)
                self._truncate_pool()
                self.mcts.backpropagate(node)

        self._iteration += 1
        await self._finish_embedding_tasks()
        self._log_generation_stats()
        return True, self._best_in_pool()

    async def evolve_generation(self, parent_population: list[Algorithm]) -> list[Algorithm]:
        """Compatibility wrapper around one MCTS iteration."""
        await self.step()
        return list(self.pool)

    # ------------------------------------------------------------------ #
    # Tree / pool helpers
    # ------------------------------------------------------------------ #

    def _add_node(self, algorithm: Algorithm, parent: MCTSNode) -> MCTSNode:
        """Wrap an algorithm in an MCTS node and attach it to a parent.

        Args:
            algorithm: The evaluated algorithm.
            parent: Parent tree node.

        Returns:
            The newly created node.
        """
        node = MCTSNode(
            algorithm_desc=algorithm.description,
            code=_code_signature(algorithm),
            q=algorithm.score,
            depth=parent.depth + 1,
            individual=algorithm,
            parent=parent,
            visits=1,
        )
        parent.add_child(node)
        return node

    def _select_parents_for_operator(self, operator: str, node: MCTSNode) -> list[Algorithm]:
        """Choose parents for an expansion operator.

        - e1: one representative algorithm from each root child's subtree.
        - e2: the selected node's algorithm plus one pool sample.
        - m1/m2: the selected node's algorithm.
        - s1: the path from the selected node up to the root.

        Args:
            operator: Operator key.
            node: The UCT-selected node.

        Returns:
            Parent algorithms for the operator (may be empty).
        """
        if node.individual is None and operator != "e1":
            return []

        if operator == "e1":
            reps: list[Algorithm] = []
            for child in self.mcts.root.children:
                pool = child.subtree or [child]
                pick = random.choice(pool)
                if pick.individual is not None:
                    reps.append(pick.individual)
            return reps or ([node.individual] if node.individual else [])

        if operator == "e2":
            parents = [node.individual]
            others = [a for a in self.pool if a.id != node.individual.id]
            if others:
                parents.append(self._select_pool(1, others)[0])
            return parents

        if operator == "s1":
            path: list[Algorithm] = []
            walk: MCTSNode | None = node
            while walk is not None and walk.individual is not None:
                path.append(walk.individual)
                walk = walk.parent
            return path

        # m1 / m2
        return [node.individual]

    def _select_pool(self, count: int, pool: list[Algorithm] | None = None) -> list[Algorithm]:
        """Rank-based selection from the active pool (probability 1/(r+1+N)).

        Args:
            count: Number of algorithms to select.
            pool: Optional explicit pool (defaults to the active pool).

        Returns:
            Selected algorithms.
        """
        import numpy as np

        candidates = pool if pool is not None else self.pool
        evaluated = [a for a in candidates if a.is_evaluated()]
        if not evaluated:
            return []
        ranked = sorted(evaluated, key=lambda a: a.score, reverse=True)
        n = len(ranked)
        weights = np.array([1.0 / (r + 1 + n) for r in range(n)])
        weights = weights / weights.sum()
        take = min(count, n)
        idx = np.random.choice(n, size=take, replace=False, p=weights)
        return [ranked[i] for i in idx]

    def _truncate_pool(self) -> None:
        """Keep the top ``pop_size`` unique algorithms in the active pool."""
        seen: set[float] = set()
        unique: list[Algorithm] = []
        for a in sorted(self.pool, key=lambda x: x.score if x.is_evaluated() else float("-inf"), reverse=True):
            key = a.score if a.is_evaluated() else float("-inf")
            if key in seen:
                continue
            seen.add(key)
            unique.append(a)
        self.pool = unique[: self.config.pop_size]

    def _is_duplicate(self, algorithm: Algorithm) -> bool:
        """Check whether an algorithm duplicates one already in the pool.

        Args:
            algorithm: Candidate algorithm.

        Returns:
            True if a duplicate (by code or score) exists.
        """
        sig = _code_signature(algorithm)
        score = algorithm.score if algorithm.is_evaluated() else None
        for a in self.pool:
            if _code_signature(a) == sig:
                return True
            if score is not None and a.is_evaluated() and a.score == score:
                return True
        return False

    def _best_in_pool(self) -> Algorithm | None:
        """Return the highest-scoring evaluated algorithm in the pool."""
        evaluated = [a for a in self.pool if a.is_evaluated()]
        if not evaluated:
            return None
        return max(evaluated, key=lambda a: a.score)

    def _count_nodes(self) -> int:
        """Count the total number of nodes in the tree (excluding root)."""
        count = 0
        stack = list(self.mcts.root.children)
        while stack:
            node = stack.pop()
            count += 1
            stack.extend(node.children)
        return count

    # ------------------------------------------------------------------ #
    # Candidate generation + evaluation
    # ------------------------------------------------------------------ #

    async def _generate(self, parents: list[Algorithm], operator: str) -> Algorithm | None:
        """Generate and evaluate one candidate (plan → implement → build → eval).

        Args:
            parents: Parent algorithms for the operator.
            operator: Operator key.

        Returns:
            The evaluated algorithm, or None on failure.
        """
        candidate_start = time.time()
        self._operator_stats.setdefault(operator, {"calls": 0, "successes": 0})
        self._operator_stats[operator]["calls"] += 1
        generation = self._iteration

        try:
            algorithm_id = uuid.uuid4().hex[:12]
            step_start = time.time()
            worktree = await self.planner.init(
                island_id=0, generation_id=generation, algorithm_id=algorithm_id,
            )
            self.state_tracker.record_timing("create_worktree", (time.time() - step_start) * 1000)
            if worktree is None:
                return None

            step_start = time.time()
            algorithm = await self.planner.plan(
                population=parents,
                generation=generation,
                operator=operator,
                parents=parents,
                background=self._background,
            )
            self.state_tracker.record_timing("generate_insight", (time.time() - step_start) * 1000)

            algorithm.id = algorithm_id
            algorithm.generation = generation
            algorithm.worktree = worktree
            algorithm.custom_metadata["mcts_ahd"] = {
                "operator": operator,
                "parent_ids": [p.id for p in parents],
            }

            if self.state_tracker.generated_dir:
                algorithm.write(self.state_tracker.generated_dir, "insight", island_id=None, generation=generation)

            step_start = time.time()
            algorithm = await self.planner.implement(
                algorithm=algorithm,
                worktree=worktree,
                task_description=self._background,
                base_context={"parent_code": parents[0]} if parents else {},
            )
            self.state_tracker.record_timing("implement_code", (time.time() - step_start) * 1000)

            step_start = time.time()
            await self.planner.build(algorithm=algorithm, worktree=worktree)
            self.state_tracker.record_timing("build_algorithm", (time.time() - step_start) * 1000)

            commit_result = self.version_control.commit_changes(
                worktree=worktree,
                message=f"feat: mcts-ahd {operator} {algorithm.name}\n\n1. Generated by MCTS-AHD",
            )
            if not commit_result.success:
                raise RuntimeError(commit_result.error or commit_result.message)

            for file_info in self.version_control.get_changed_files(commit_hash=worktree.commit_hash):
                algorithm.add_code_artifact(
                    file_path=file_info.get("file_path", ""),
                    content=file_info.get("content", ""),
                    content_mode="full",
                )

            # Evaluate immediately (MCTS needs the score before backprop).
            await self._evaluate(algorithm)

            algorithm.timing.wall_time_ms = (time.time() - candidate_start) * 1000
            algorithm.timing.recompute_overhead()
            self.state_tracker.record_candidate_timing(algorithm.id, algorithm.timing)
            self._operator_stats[operator]["successes"] += 1
            return algorithm
        except Exception as exc:
            logger.warning(f"[MCTS-AHD] Skipping candidate for operator {operator}: {exc}")
            return None

    async def _evaluate(self, algorithm: Algorithm) -> None:
        """Evaluate one algorithm and apply the result.

        Args:
            algorithm: Algorithm to evaluate in place.
        """
        eval_start = time.time()
        results = await self.dispatcher.dispatch_batch(algorithms=[algorithm])
        self.state_tracker.record_timing("evaluate_batch", (time.time() - eval_start) * 1000)
        if not results:
            self._eval_failures += 1
            return
        result: EvaluationResult = results[0]
        algorithm.set_evaluation_result(
            score=result.score,
            metrics=dict(result.metrics),
            error=result.error_message if not result.success else None,
            evaluation_time_ms=result.duration_ms,
            timing=ExecutionTiming(evaluation_total_ms=result.duration_ms),
        )
        algorithm.custom_metadata["evaluation_result"] = result.model_dump(mode="json")
        if algorithm.is_evaluated():
            self._eval_successes += 1
            if self.state_tracker.generated_dir:
                algorithm.write(
                    self.state_tracker.generated_dir, "evaluation", island_id=None, generation=algorithm.generation
                )
        else:
            self._eval_failures += 1
            logger.warning(
                f"[MCTS-AHD] Evaluation failed for {algorithm.id}: {result.error_message} (score={result.score})"
            )

    # ------------------------------------------------------------------ #
    # Checkpoint / status / logging
    # ------------------------------------------------------------------ #

    async def pause(self) -> None:
        """Pause the MCTS-AHD run."""
        self.state = EvolutionState.PAUSED

    async def resume(self) -> None:
        """Resume a paused MCTS-AHD run."""
        if self.state == EvolutionState.PAUSED:
            self.state = EvolutionState.RUNNING

    async def save_checkpoint(self, path: str | None = None) -> str:
        """Persist a lightweight MCTS-AHD checkpoint (active pool only)."""
        checkpoint = EvolutionCheckpoint(
            generation=self._iteration,
            population=self.pool,
            best_individual=self._best_in_pool(),
            history=self.history,
            metadata={"total_samples": self.total_samples, "pool_size": self.config.pop_size},
        )
        checkpoint_dir = getattr(self.config, "checkpoint_dir", None) or "."
        checkpoint_path = Path(path or (Path(checkpoint_dir) / f"mcts_ahd_{self._iteration}.json"))
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
        return str(checkpoint_path)

    async def load_checkpoint(self, path: str) -> EvolutionCheckpoint:
        """Load an MCTS-AHD checkpoint (restores the active pool, not the tree)."""
        checkpoint = EvolutionCheckpoint.model_validate_json(Path(path).read_text(encoding="utf-8"))
        self.pool = list(checkpoint.population)
        self._iteration = checkpoint.generation
        self.best_individual = checkpoint.best_individual
        self.history = checkpoint.history
        self.total_samples = checkpoint.metadata.get("total_samples", 0)
        return checkpoint

    def get_status(self) -> dict[str, Any]:
        """Return current MCTS-AHD status."""
        best = self._best_in_pool()
        return {
            "state": self.state.value,
            "current_generation": self._iteration,
            "total_samples": self.total_samples,
            "pool_size": len(self.pool),
            "tree_nodes": self._count_nodes(),
            "best_score": best.score if best else None,
            **self.state_tracker.get_status_summary(),
        }

    def _log_generation_stats(self) -> None:
        """Record and log statistics for the current MCTS iteration."""
        evaluated = [a for a in self.pool if a.is_evaluated()]
        if evaluated:
            scores = [a.score for a in evaluated]
            best_score = max(scores)
            average_score = sum(scores) / len(scores)
            worst_score = min(scores)
        else:
            best_score = float("-inf")
            average_score = 0.0
            worst_score = float("inf")

        best = self._best_in_pool()
        self.best_individual = best

        stats = {
            "generation": self._iteration,
            "best_score": best.score if best else None,
            "best_name": best.name if best else None,
            "average_score": average_score,
            "pool_size": len(self.pool),
            "tree_nodes": self._count_nodes(),
            "samples": self.total_samples,
        }
        self.history.append(stats)
        self.monitor.log_generation(stats)

        metrics = GenerationMetrics(
            generation=self._iteration,
            best_score=best_score,
            average_score=average_score,
            worst_score=worst_score,
            total_evaluations=self.total_samples,
            successful_evaluations=len(evaluated),
            failed_evaluations=max(0, self.total_samples - len(evaluated)),
        )
        self.state_tracker.update_generation(self._iteration, metrics)

        logger.info(
            f"[MCTS-AHD] iter={self._iteration} pool={len(self.pool)} nodes={self._count_nodes()} "
            f"best={best_score:.4f} avg={average_score:.4f} samples={self.total_samples} "
            f"eval_ok={self._eval_successes} eval_fail={self._eval_failures}"
        )

    def _log_timing_summary(self) -> None:
        """Log a formatted table of accumulated module timing statistics."""
        module_timing = self.state_tracker.module_timing
        if not module_timing:
            return
        header = f"{'Module':<26} {'Calls':>6} {'Avg':>12} {'Total':>12}"
        sep = "-" * len(header)
        lines = ["", "[MCTS-AHD] Timing Summary:", sep, header, sep]
        for key, mt in module_timing.items():
            lines.append(
                f"{key:<26} {mt.call_count:>6} "
                f"{format_duration_ms(mt.average_time_ms):>12} "
                f"{format_duration_ms(mt.total_time_ms):>12}"
            )
        lines.append(sep)
        logger.info("\n".join(lines))
