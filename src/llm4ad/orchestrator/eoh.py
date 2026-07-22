"""EoH (Evolution of Heuristics) orchestrator implementation.

Single-objective sibling of MEoH. Migrated from the legacy LLM4AD method
``llm4ad/method/eoh``. Reuses the ``meoh_evolution`` planner and the
``meoh_*`` operator samplers, but drives them with a generational,
single-objective loop:

1. Initialize a population with the I1 operator.
2. Each generation, run E1 (and optionally E2/M1/M2) sequentially; each
   operator produces offspring that are scored on a single fitness value.
3. Survival is rank-based top-k truncation over ``score``.

Parent selection uses the rank-based probability ``1 / (r + N)`` from the
original EoH population, favoring higher-ranked individuals.

Reference:
    Fei Liu, Tong Xialiang, Mingxuan Yuan, Xi Lin, Fu Luo, Zhenkun Wang,
    Zhichao Lu, Qingfu Zhang. "Evolution of Heuristics: Towards Efficient
    Automatic Algorithm Design Using Large Language Model." ICML 2024.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from llm4ad.coder.base import BaseCoder
from llm4ad.config.evolution import EoHConfig
from llm4ad.evaluator import EvaluationDispatcher, EvaluationResult
from llm4ad.infra.state import EvolutionState, GenerationMetrics, StateTracker
from llm4ad.infra.timing import ExecutionTiming
from llm4ad.infra.version_control.base import BaseVersionControl
from llm4ad.orchestrator.base import BaseOrchestrator, EvolutionCheckpoint, EvolutionResult, format_duration_ms
from llm4ad.orchestrator.embedding_client import EmbeddingClient
from llm4ad.orchestrator.embedding_utils import save_algorithm_embeddings
from llm4ad.planner.base import Algorithm, BasePlanner
from llm4ad.planner.eoh_evolution import EoHEvolutionPlanner


class EoHPopulation:
    """Single-objective, rank-based population for EoH.

    Maintains up to ``pop_size`` evaluated individuals sorted by ``score``
    (higher is better). Selection is rank-based with probability
    ``1 / (r + N)`` for the individual at rank ``r`` (0 = best), matching the
    original EoH implementation.
    """

    def __init__(self, pop_size: int) -> None:
        """Initialize an empty population.

        Args:
            pop_size: Maximum number of individuals to retain after survival.
        """
        self.pop_size = pop_size
        self.population: list[Algorithm] = []
        self.generation = 0

    def __len__(self) -> int:
        """Return the number of individuals currently in the population."""
        return len(self.population)

    def has_duplicate(self, algorithm: Algorithm) -> bool:
        """Check whether an algorithm duplicates an existing member.

        Two individuals are considered duplicates if their concatenated code
        artifacts are identical or their scores are exactly equal (the latter
        mirrors the original EoH heuristic for near-identical heuristics).

        Args:
            algorithm: Candidate algorithm to check.

        Returns:
            True if a duplicate already exists in the population.
        """
        code_sig = _code_signature(algorithm)
        cand_score = algorithm.score if algorithm.is_evaluated() else None
        for existing in self.population:
            if _code_signature(existing) == code_sig:
                return True
            if (
                cand_score is not None
                and existing.is_evaluated()
                and existing.score == cand_score
            ):
                return True
        return False

    def register_init_individual(self, algorithm: Algorithm) -> bool:
        """Register an individual during population initialization.

        Only successfully evaluated, non-duplicate individuals are accepted.

        Args:
            algorithm: Candidate produced by the I1 operator.

        Returns:
            True if the individual was added to the population.
        """
        if not algorithm.is_evaluated() or algorithm.score is None:
            return False
        if self.has_duplicate(algorithm):
            return False
        self.population.append(algorithm)
        return True

    def register_offspring(self, algorithm: Algorithm) -> bool:
        """Register an offspring produced by an evolution operator.

        Unevaluated or duplicate individuals are rejected. After acceptance,
        survival truncation keeps the top ``pop_size`` individuals.

        Args:
            algorithm: Offspring candidate.

        Returns:
            True if the individual was added (before truncation).
        """
        if not algorithm.is_evaluated() or algorithm.score is None:
            return False
        if self.has_duplicate(algorithm):
            return False
        self.population.append(algorithm)
        self.survival()
        return True

    def survival(self) -> None:
        """Truncate to the top ``pop_size`` individuals by score (desc)."""
        evaluated = [a for a in self.population if a.is_evaluated()]
        evaluated.sort(key=lambda a: a.score, reverse=True)
        self.population = evaluated[: self.pop_size]

    def selection(self, count: int) -> list[Algorithm]:
        """Select ``count`` parents using rank-based probabilities.

        The individual at rank ``r`` (0 = best) is chosen with probability
        proportional to ``1 / (r + N)`` where ``N`` is the population size.
        Sampling is without replacement when possible.

        Args:
            count: Number of parents to select.

        Returns:
            List of selected parent algorithms (may be shorter than ``count``
            if the population is small).
        """
        evaluated = [a for a in self.population if a.is_evaluated()]
        if not evaluated:
            return []

        ranked = sorted(evaluated, key=lambda a: a.score, reverse=True)
        n = len(ranked)
        weights = np.array([1.0 / (r + n) for r in range(n)])
        weights = weights / weights.sum()

        take = min(count, n)
        idx = np.random.choice(n, size=take, replace=False, p=weights)
        return [ranked[i] for i in idx]

    def best(self) -> Algorithm | None:
        """Return the highest-scoring evaluated individual, or None."""
        evaluated = [a for a in self.population if a.is_evaluated()]
        if not evaluated:
            return None
        return max(evaluated, key=lambda a: a.score)


def _code_signature(algorithm: Algorithm) -> str:
    """Build a stable code signature for duplicate detection.

    Args:
        algorithm: Algorithm whose code artifacts are concatenated.

    Returns:
        Concatenated artifact contents (empty string if none).
    """
    return "\n".join(a.content for a in algorithm.code_artifacts)


@BaseOrchestrator.register("eoh")
class EoHOrchestrator(BaseOrchestrator):
    """Single-objective Evolution of Heuristics orchestrator."""

    def __init__(
        self,
        planner: BasePlanner,
        coder: BaseCoder,
        dispatcher: EvaluationDispatcher,
        monitor: Any,
        config: EoHConfig,
        version_control: BaseVersionControl,
        state_tracker: StateTracker,
        background: str = "",
        embedding_client: EmbeddingClient = None,
    ) -> None:
        """Initialize the EoH orchestrator.

        Args:
            planner: Must be an ``EoHEvolutionPlanner`` (operator dispatch).
            coder: Coder used by the planner's ``implement``.
            dispatcher: Evaluation dispatcher.
            monitor: Progress monitor.
            config: EoH configuration.
            version_control: Worktree manager.
            state_tracker: Run state / timing tracker.
            background: Problem background passed to samplers.
            embedding_client: Optional embedding client for insight vectors.
        """
        super().__init__(planner, coder, dispatcher, monitor, config, state_tracker, background, embedding_client)
        if not isinstance(planner, EoHEvolutionPlanner):
            raise TypeError("EoHOrchestrator requires an EoHEvolutionPlanner (operator dispatch)")
        self.planner: EoHEvolutionPlanner = planner
        self.config: EoHConfig = config
        self.version_control = version_control
        self.eoh_population = EoHPopulation(pop_size=config.population_size)
        self._background = background or getattr(config, "background", "")
        self.total_samples = 0

        self._llm_semaphore: asyncio.Semaphore | None = None
        if getattr(config, "max_llm_concurrency", None) is not None:
            self._llm_semaphore = asyncio.Semaphore(config.max_llm_concurrency)

        # Diagnostics
        self._planner_calls = 0
        self._coder_calls = 0
        self._eval_successes = 0
        self._eval_failures = 0
        self._duplicates_rejected = 0
        self._operator_stats: dict[str, dict[str, int]] = {}

    @property  # type: ignore[override]
    def current_generation(self) -> int:  # type: ignore[override]
        """Single source of truth for the current generation counter."""
        return self.eoh_population.generation

    @current_generation.setter
    def current_generation(self, value: int) -> None:
        """Allow base-class assignments without error (generation is owned by the population)."""
        pass

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #

    async def run(self) -> EvolutionResult:
        """Run EoH until a termination condition is met.

        Returns:
            EvolutionResult with the best individual and final population.
        """
        self.state = EvolutionState.RUNNING
        self.start_time = time.time()
        self.state_tracker.start_run(self.config.max_generations)
        try:
            if not self.eoh_population.population:
                await self.initialize_population()
                if len(self.eoh_population) < self.config.selection_num:
                    logger.warning(
                        f"[EoH] Initialization produced only {len(self.eoh_population)} "
                        f"individuals (< selection_num={self.config.selection_num}); stopping."
                    )
                self._log_generation_stats()
            while self.state == EvolutionState.RUNNING:
                if self.current_generation >= self.config.max_generations:
                    break
                if self.total_samples >= self.config.max_sample_nums:
                    break
                if len(self.eoh_population) < self.config.selection_num:
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

        best = self.eoh_population.best()
        self.best_individual = best
        return EvolutionResult(
            state=self.state,
            best_individual=best,
            final_population=list(self.eoh_population.population),
            final_generation=self.current_generation,
            total_evaluations=self.total_samples,
            history=self.history,
            duration_seconds=time.time() - self.start_time,
            metadata={
                "population_size": len(self.eoh_population.population),
                "best_score": best.score if best else None,
            },
        )

    async def initialize_population(self) -> list[Algorithm]:
        """Initialize the population with the I1 operator.

        Repeatedly generates candidates in batches until ``pop_size`` unique
        evaluated individuals exist or the sample budget is exhausted.

        Returns:
            The initialized population.
        """
        if self.config.seed_path:
            await self._load_seed_population(self.config.seed_path)
            if self.eoh_population.population:
                self.population = list(self.eoh_population.population)
                self.best_individual = self.eoh_population.best()
                return self.population

        init_target = self.config.population_size
        # Cap the number of init trials (mirrors legacy initial_sample_nums_max)
        max_init_samples = min(self.config.max_sample_nums, 2 * init_target)
        init_start = time.time()

        while len(self.eoh_population) < init_target and self.total_samples < max_init_samples:
            remaining = init_target - len(self.eoh_population)
            budget = max_init_samples - self.total_samples
            batch_size = max(1, min(remaining, budget, max(1, self.config.num_samplers)))

            results = await asyncio.gather(*[
                self._limited_generate([], "i1", self.current_generation)
                for _ in range(batch_size)
            ])

            candidates = [r for r in results if r is not None]
            if candidates:
                await self._batch_evaluate(candidates)

            for algorithm in results:
                self.total_samples += 1
                if algorithm is None:
                    continue
                if not self.eoh_population.register_init_individual(algorithm):
                    self._duplicates_rejected += 1

        self.state_tracker.record_timing("init_population", (time.time() - init_start) * 1000)
        self.population = list(self.eoh_population.population)
        self.best_individual = self.eoh_population.best()
        logger.info(
            f"[EoH] Initialized population with {len(self.eoh_population)}/{init_target} "
            f"individuals after {self.total_samples} samples"
        )
        return self.population

    async def step(self) -> tuple[bool, Algorithm | None]:
        """Execute one EoH generation: run each enabled operator in turn.

        For each operator, ``num_samplers`` offspring are generated in
        parallel, batch-evaluated, then registered (with survival) in order.

        Returns:
            Tuple of (should_continue, current best individual).
        """
        if self.state != EvolutionState.RUNNING:
            return False, self.eoh_population.best()

        for operator in self._enabled_operators():
            if self.total_samples >= self.config.max_sample_nums:
                break

            n = max(1, self.config.num_samplers)
            tasks_info = [(operator, self._select_parents_for_operator(operator)) for _ in range(n)]

            generation = self.current_generation
            results = await asyncio.gather(*[
                self._limited_generate(parents, op, generation)
                for op, parents in tasks_info
            ])

            candidates = [r for r in results if r is not None]
            if candidates:
                await self._batch_evaluate(candidates)

            for algorithm in results:
                self.total_samples += 1
                if algorithm is None:
                    continue
                if not self.eoh_population.register_offspring(algorithm):
                    self._duplicates_rejected += 1

        self.eoh_population.generation += 1
        await self._finish_embedding_tasks()
        self._log_generation_stats()
        return True, self.eoh_population.best()

    async def evolve_generation(self, parent_population: list[Algorithm]) -> list[Algorithm]:
        """Compatibility wrapper around one EoH step.

        Args:
            parent_population: Ignored; the internal population is used.

        Returns:
            The population after one step.
        """
        await self.step()
        return list(self.eoh_population.population)

    # ------------------------------------------------------------------ #
    # Candidate generation (plan -> implement -> build -> commit)
    # ------------------------------------------------------------------ #

    async def _limited_generate(
        self,
        parents: list[Algorithm],
        operator: str,
        generation: int,
    ) -> Algorithm | None:
        """Generate one candidate, optionally bounded by the LLM semaphore."""
        if self._llm_semaphore is not None:
            async with self._llm_semaphore:
                return await self.generate_new_individual(parents, operator, generation)
        return await self.generate_new_individual(parents, operator, generation)

    async def generate_new_individual(
        self,
        parents: list[Algorithm],
        operator: str,
        generation: int,
    ) -> Algorithm | None:
        """Generate one EoH candidate via plan → implement → build → commit.

        Evaluation is performed separately by ``_batch_evaluate``.

        Args:
            parents: Parent individuals for the operator (empty for i1).
            operator: One of i1/e1/e2/m1/m2.
            generation: Current generation number.

        Returns:
            The generated algorithm, or None on failure.
        """
        candidate_start = time.time()
        self._operator_stats.setdefault(operator, {"calls": 0, "successes": 0})
        self._operator_stats[operator]["calls"] += 1

        try:
            algorithm_id = uuid.uuid4().hex[:12]

            step_start = time.time()
            worktree = await self.planner.init(
                island_id=0, generation_id=generation, algorithm_id=algorithm_id,
            )
            self.state_tracker.record_timing("create_worktree", (time.time() - step_start) * 1000)
            if worktree is None:
                return None

            # Plan (insight via operator sampler)
            self._planner_calls += 1
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
            algorithm.custom_metadata["eoh"] = {
                "operator": operator,
                "parent_ids": [p.id for p in parents],
            }

            if self.state_tracker.generated_dir:
                algorithm.write(self.state_tracker.generated_dir, "insight", island_id=None, generation=generation)
                if self.embedding_client:
                    task = asyncio.create_task(
                        save_algorithm_embeddings(self.embedding_client, algorithm, self.state_tracker.embedding_dir))
                    self._embedding_tasks.add(task)
                    task.add_done_callback(self._embedding_tasks.discard)

            # Implement (coder generates code from insight)
            self._coder_calls += 1
            step_start = time.time()
            algorithm = await self.planner.implement(
                algorithm=algorithm,
                worktree=worktree,
                task_description=self._background,
                base_context={"parent_code": parents[0]} if parents else {},
            )
            self.state_tracker.record_timing("implement_code", (time.time() - step_start) * 1000)

            # Build verification
            step_start = time.time()
            await self.planner.build(algorithm=algorithm, worktree=worktree)
            self.state_tracker.record_timing("build_algorithm", (time.time() - step_start) * 1000)

            # Commit and collect code artifacts
            commit_result = self.version_control.commit_changes(
                worktree=worktree,
                message=f"feat: eoh {operator} {algorithm.name}\n\n1. Generated by EoH",
            )
            if not commit_result.success:
                raise RuntimeError(commit_result.error or commit_result.message)

            for file_info in self.version_control.get_changed_files(commit_hash=worktree.commit_hash):
                algorithm.add_code_artifact(
                    file_path=file_info.get("file_path", ""),
                    content=file_info.get("content", ""),
                    content_mode="full",
                )

            algorithm.timing.wall_time_ms = (time.time() - candidate_start) * 1000
            algorithm.timing.recompute_overhead()
            self.state_tracker.record_candidate_timing(algorithm.id, algorithm.timing)
            self._operator_stats[operator]["successes"] += 1
            return algorithm
        except Exception as exc:
            logger.warning(f"[EoH] Skipping candidate for operator {operator}: {exc}")
            return None

    async def _batch_evaluate(self, algorithms: list[Algorithm]) -> list[Algorithm]:
        """Batch-evaluate candidates via a single dispatcher call.

        Args:
            algorithms: Candidates to evaluate.

        Returns:
            The same list, with evaluation results applied.
        """
        to_evaluate = [a for a in algorithms if not a.is_evaluated() and a.worktree is not None]
        if not to_evaluate:
            return algorithms

        eval_start = time.time()
        results = await self.dispatcher.dispatch_batch(algorithms=to_evaluate)
        self.state_tracker.record_timing("evaluate_batch", (time.time() - eval_start) * 1000)

        for alg, result in zip(to_evaluate, results, strict=True):
            self._apply_eval_result(alg, result)
            if result.success and self.state_tracker.generated_dir:
                alg.write(self.state_tracker.generated_dir, "evaluation", island_id=None, generation=alg.generation)
        return algorithms

    def _apply_eval_result(self, algorithm: Algorithm, result: EvaluationResult) -> None:
        """Apply an evaluation result to an algorithm and update counters.

        Args:
            algorithm: Algorithm to update.
            result: Dispatcher evaluation result.
        """
        if not result.success:
            self._eval_failures += 1
            logger.warning(
                f"[EoH] Evaluation failed for {algorithm.id}: {result.error_message}"
            )
            return

        algorithm.set_evaluation_result(
            score=result.score,
            metrics=dict(result.metrics),
            error=None,
            evaluation_time_ms=result.duration_ms,
            timing=ExecutionTiming(evaluation_total_ms=result.duration_ms),
        )
        algorithm.custom_metadata["evaluation_result"] = result.model_dump(mode="json")
        self._eval_successes += 1

    # ------------------------------------------------------------------ #
    # Operators / selection
    # ------------------------------------------------------------------ #

    def _enabled_operators(self) -> list[str]:
        """Return the enabled evolution operators for a generation.

        Returns:
            Ordered list of operators (e1 always enabled).
        """
        operators = ["e1"]
        if self.config.use_e2_operator:
            operators.append("e2")
        if self.config.use_m1_operator:
            operators.append("m1")
        if self.config.use_m2_operator:
            operators.append("m2")
        return operators

    def _select_parents_for_operator(self, operator: str) -> list[Algorithm]:
        """Select parents for an operator (crossover needs multiple, mutation one).

        Args:
            operator: Operator key.

        Returns:
            Selected parents.
        """
        if operator in {"e1", "e2"}:
            return self.eoh_population.selection(self.config.selection_num)
        return self.eoh_population.selection(1)

    # ------------------------------------------------------------------ #
    # Checkpoint / status / logging
    # ------------------------------------------------------------------ #

    async def pause(self) -> None:
        """Pause the EoH run."""
        self.state = EvolutionState.PAUSED

    async def resume(self) -> None:
        """Resume a paused EoH run."""
        if self.state == EvolutionState.PAUSED:
            self.state = EvolutionState.RUNNING

    async def save_checkpoint(self, path: str | None = None) -> str:
        """Persist a lightweight EoH checkpoint.

        Args:
            path: Optional explicit path.

        Returns:
            The checkpoint file path.
        """
        checkpoint = EvolutionCheckpoint(
            generation=self.current_generation,
            population=self.eoh_population.population,
            best_individual=self.eoh_population.best(),
            history=self.history,
            metadata={"total_samples": self.total_samples, "pop_size": self.eoh_population.pop_size},
        )
        checkpoint_dir = getattr(self.config, "checkpoint_dir", None) or "."
        checkpoint_path = Path(path or (Path(checkpoint_dir) / f"eoh_{self.current_generation}.json"))
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
        return str(checkpoint_path)

    async def load_checkpoint(self, path: str) -> EvolutionCheckpoint:
        """Load an EoH checkpoint.

        Args:
            path: Checkpoint file path.

        Returns:
            The loaded checkpoint.
        """
        checkpoint = EvolutionCheckpoint.model_validate_json(Path(path).read_text(encoding="utf-8"))
        self.eoh_population.population = list(checkpoint.population)
        self.eoh_population.generation = checkpoint.generation
        self.best_individual = checkpoint.best_individual
        self.history = checkpoint.history
        self.total_samples = checkpoint.metadata.get("total_samples", 0)
        return checkpoint

    def get_status(self) -> dict[str, Any]:
        """Return current EoH status.

        Returns:
            Status summary dict.
        """
        best = self.eoh_population.best()
        return {
            "state": self.state.value,
            "current_generation": self.current_generation,
            "total_samples": self.total_samples,
            "population_size": len(self.eoh_population.population),
            "best_score": best.score if best else None,
            **self.state_tracker.get_status_summary(),
        }

    async def _load_seed_population(self, seed_path: str) -> None:
        """Load seed algorithms from a JSON file.

        Args:
            seed_path: Path to a JSON list (or ``{"algorithms": [...]}``).
        """
        raw = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        payload = raw if isinstance(raw, list) else raw.get("algorithms", [])
        algorithms = [Algorithm.model_validate(item) for item in payload]
        self.eoh_population.population = algorithms[: self.config.population_size]

    def _log_generation_stats(self) -> None:
        """Record and log statistics for the current generation."""
        evaluated = [a for a in self.eoh_population.population if a.is_evaluated()]
        if evaluated:
            scores = [a.score for a in evaluated]
            best_score = max(scores)
            average_score = sum(scores) / len(scores)
            worst_score = min(scores)
        else:
            best_score = float("-inf")
            average_score = 0.0
            worst_score = float("inf")

        best = self.eoh_population.best()
        self.best_individual = best

        stats = {
            "generation": self.current_generation,
            "best_score": best.score if best else None,
            "best_name": best.name if best else None,
            "average_score": average_score,
            "population_size": len(self.eoh_population.population),
            "samples": self.total_samples,
        }
        self.history.append(stats)
        self.monitor.log_generation(stats)

        metrics = GenerationMetrics(
            generation=self.current_generation,
            best_score=best_score,
            average_score=average_score,
            worst_score=worst_score,
            total_evaluations=self.total_samples,
            successful_evaluations=len(evaluated),
            failed_evaluations=max(0, self.total_samples - len(evaluated)),
        )
        self.state_tracker.update_generation(self.current_generation, metrics)

        logger.info(
            f"[EoH] gen={self.current_generation} pop={len(self.eoh_population.population)} "
            f"best={best_score:.4f} avg={average_score:.4f} samples={self.total_samples} "
            f"eval_ok={self._eval_successes} eval_fail={self._eval_failures} "
            f"dup={self._duplicates_rejected}"
        )

    def _log_timing_summary(self) -> None:
        """Log a formatted table of accumulated module timing statistics."""
        module_timing = self.state_tracker.module_timing
        if not module_timing:
            return
        header = f"{'Module':<26} {'Calls':>6} {'Avg':>12} {'Total':>12}"
        sep = "-" * len(header)
        lines = ["", "[EoH] Timing Summary:", sep, header, sep]
        for key, mt in module_timing.items():
            lines.append(
                f"{key:<26} {mt.call_count:>6} "
                f"{format_duration_ms(mt.average_time_ms):>12} "
                f"{format_duration_ms(mt.total_time_ms):>12}"
            )
        lines.append(sep)
        logger.info("\n".join(lines))
