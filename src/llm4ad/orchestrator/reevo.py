"""ReEvo (Reflective Evolution) orchestrator implementation.

Migrated from the legacy LLM4AD ``method/reevo``. Structurally similar to
the single-objective EoH loop (rank-based selection, top-k survival) but the
generation step is reflection-driven:

1. Generate a batch of crossover offspring; each crossover first performs a
   short-term reflection (worse vs better parent) inside its sampler.
2. Accumulate the short-term reflections, then distil them into a long-term
   reflection via the planner.
3. Generate a batch of elite mutations guided by the long-term reflection.

Reference:
    Ye et al. "ReEvo: Large Language Models as Hyper-Heuristics with
    Reflective Evolution." NeurIPS 2024.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from llm4ad.coder.base import BaseCoder
from llm4ad.config.evolution import ReEvoConfig
from llm4ad.evaluator import EvaluationDispatcher, EvaluationResult
from llm4ad.infra.state import EvolutionState, GenerationMetrics, StateTracker
from llm4ad.infra.timing import ExecutionTiming
from llm4ad.infra.version_control.base import BaseVersionControl
from llm4ad.orchestrator.base import BaseOrchestrator, EvolutionCheckpoint, EvolutionResult, format_duration_ms
from llm4ad.orchestrator.embedding_client import EmbeddingClient
from llm4ad.orchestrator.embedding_utils import save_algorithm_embeddings
from llm4ad.orchestrator.eoh import EoHPopulation
from llm4ad.planner.base import Algorithm, BasePlanner
from llm4ad.planner.reevo_evolution import ReEvoEvolutionPlanner


@BaseOrchestrator.register("reevo")
class ReEvoOrchestrator(BaseOrchestrator):
    """Reflective Evolution orchestrator."""

    def __init__(
        self,
        planner: BasePlanner,
        coder: BaseCoder,
        dispatcher: EvaluationDispatcher,
        monitor: Any,
        config: ReEvoConfig,
        version_control: BaseVersionControl,
        state_tracker: StateTracker,
        background: str = "",
        embedding_client: EmbeddingClient = None,
    ) -> None:
        """Initialize the ReEvo orchestrator.

        Args:
            planner: Must be a ``ReEvoEvolutionPlanner``.
            coder: Coder used by the planner's ``implement``.
            dispatcher: Evaluation dispatcher.
            monitor: Progress monitor.
            config: ReEvo configuration.
            version_control: Worktree manager.
            state_tracker: Run state / timing tracker.
            background: Problem background passed to samplers.
            embedding_client: Optional embedding client.
        """
        super().__init__(planner, coder, dispatcher, monitor, config, state_tracker, background, embedding_client)
        if not isinstance(planner, ReEvoEvolutionPlanner):
            raise TypeError("ReEvoOrchestrator requires a ReEvoEvolutionPlanner")
        self.planner: ReEvoEvolutionPlanner = planner
        self.config: ReEvoConfig = config
        self.version_control = version_control
        self.reevo_population = EoHPopulation(pop_size=config.pop_size)
        self._background = background or getattr(config, "background", "")
        self.total_samples = 0

        # Reflection state
        self._short_term_reflections: list[str] = []
        self._long_term_reflection: str = ""

        self._llm_semaphore: asyncio.Semaphore | None = None
        if getattr(config, "max_llm_concurrency", None) is not None:
            self._llm_semaphore = asyncio.Semaphore(config.max_llm_concurrency)

        self._eval_successes = 0
        self._eval_failures = 0
        self._duplicates_rejected = 0
        self._operator_stats: dict[str, dict[str, int]] = {}

    @property  # type: ignore[override]
    def current_generation(self) -> int:  # type: ignore[override]
        """Single source of truth for the current generation counter."""
        return self.reevo_population.generation

    @current_generation.setter
    def current_generation(self, value: int) -> None:
        """Ignore base-class assignments (generation owned by population)."""
        pass

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #

    async def run(self) -> EvolutionResult:
        """Run ReEvo until a termination condition is met.

        Returns:
            EvolutionResult with the best individual and final population.
        """
        self.state = EvolutionState.RUNNING
        self.start_time = time.time()
        self.state_tracker.start_run(self.config.max_generations)
        try:
            if not self.reevo_population.population:
                await self.initialize_population()
                self._log_generation_stats()
            while self.state == EvolutionState.RUNNING:
                if self.current_generation >= self.config.max_generations:
                    break
                if self.total_samples >= self.config.max_sample_nums:
                    break
                if len(self.reevo_population) < 2:
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

        best = self.reevo_population.best()
        self.best_individual = best
        return EvolutionResult(
            state=self.state,
            best_individual=best,
            final_population=list(self.reevo_population.population),
            final_generation=self.current_generation,
            total_evaluations=self.total_samples,
            history=self.history,
            duration_seconds=time.time() - self.start_time,
            metadata={
                "population_size": len(self.reevo_population.population),
                "best_score": best.score if best else None,
                "long_term_reflection": self._long_term_reflection,
            },
        )

    async def initialize_population(self) -> list[Algorithm]:
        """Initialize the population with the init operator.

        Returns:
            The initialized population.
        """
        if self.config.seed_path:
            await self._load_seed_population(self.config.seed_path)
            if self.reevo_population.population:
                self.population = list(self.reevo_population.population)
                self.best_individual = self.reevo_population.best()
                return self.population

        init_target = self.config.pop_size
        max_init_samples = min(self.config.max_sample_nums, 2 * init_target)
        init_start = time.time()

        while len(self.reevo_population) < init_target and self.total_samples < max_init_samples:
            remaining = init_target - len(self.reevo_population)
            budget = max_init_samples - self.total_samples
            batch_size = max(1, min(remaining, budget, max(1, self.config.num_samplers)))

            results = await asyncio.gather(*[
                self._limited_generate([], "init", self.current_generation)
                for _ in range(batch_size)
            ])
            candidates = [r for r in results if r is not None]
            if candidates:
                await self._batch_evaluate(candidates)
            for algorithm in results:
                self.total_samples += 1
                if algorithm is None:
                    continue
                if not self.reevo_population.register_init_individual(algorithm):
                    self._duplicates_rejected += 1

        self.state_tracker.record_timing("init_population", (time.time() - init_start) * 1000)
        self.population = list(self.reevo_population.population)
        self.best_individual = self.reevo_population.best()
        logger.info(
            f"[ReEvo] Initialized population with {len(self.reevo_population)}/{init_target} "
            f"individuals after {self.total_samples} samples"
        )
        return self.population

    async def step(self) -> tuple[bool, Algorithm | None]:
        """Execute one ReEvo generation: crossover batch → long-term reflect → mutation batch.

        Returns:
            Tuple of (should_continue, current best individual).
        """
        if self.state != EvolutionState.RUNNING:
            return False, self.reevo_population.best()

        generation = self.current_generation

        # --- 1. Crossover batch (each does short-term reflection internally) ---
        n_crossover = max(1, self.config.pop_size)
        tasks_info = []
        for _ in range(n_crossover):
            if self.total_samples + len(tasks_info) >= self.config.max_sample_nums:
                break
            parents = self.reevo_population.selection(2)
            if len(parents) < 2:
                break
            tasks_info.append(parents)

        if tasks_info:
            results = await asyncio.gather(*[
                self._limited_generate(parents, "crossover", generation)
                for parents in tasks_info
            ])
            candidates = [r for r in results if r is not None]
            if candidates:
                await self._batch_evaluate(candidates)
            for algorithm in results:
                self.total_samples += 1
                if algorithm is None:
                    continue
                reflection = algorithm.custom_metadata.get("reevo_short_term_reflection", "")
                if reflection:
                    self._short_term_reflections.append(reflection)
                if not self.reevo_population.register_offspring(algorithm):
                    self._duplicates_rejected += 1

        # --- 2. Long-term reflection from accumulated short-term hints ---
        if self._short_term_reflections:
            recent = self._short_term_reflections[-5:]
            self._long_term_reflection = await self.planner.reflect_long_term(
                self._background, self._long_term_reflection, recent
            )

        # --- 3. Elite mutation batch guided by long-term reflection ---
        n_mutation = max(1, int(self.config.mutation_rate * self.config.pop_size))
        results = []
        for mutation_idx in range(n_mutation):
            if self.total_samples + mutation_idx >= self.config.max_sample_nums:
                break
            elite = self.reevo_population.best()
            if elite is None:
                break
            results.append(await self._limited_generate([elite], "mutation", generation))

        candidates = [r for r in results if r is not None]
        if candidates:
            await self._batch_evaluate(candidates)
        for algorithm in results:
            self.total_samples += 1
            if algorithm is None:
                continue
            if not self.reevo_population.register_offspring(algorithm):
                self._duplicates_rejected += 1

        self.reevo_population.generation += 1
        await self._finish_embedding_tasks()
        self._log_generation_stats()
        return True, self.reevo_population.best()

    async def evolve_generation(self, parent_population: list[Algorithm]) -> list[Algorithm]:
        """Compatibility wrapper around one ReEvo step."""
        await self.step()
        return list(self.reevo_population.population)

    # ------------------------------------------------------------------ #
    # Candidate generation
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
        """Generate one ReEvo candidate via plan → implement → build → commit.

        Args:
            parents: Parent individuals (empty for init).
            operator: init / crossover / mutation.
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

            step_start = time.time()
            algorithm = await self.planner.plan(
                population=parents,
                generation=generation,
                operator=operator,
                parents=parents,
                background=self._background,
                long_term_reflection=self._long_term_reflection,
            )
            self.state_tracker.record_timing("generate_insight", (time.time() - step_start) * 1000)

            algorithm.id = algorithm_id
            algorithm.generation = generation
            algorithm.worktree = worktree
            algorithm.custom_metadata["reevo"] = {
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
                message=f"feat: reevo {operator} {algorithm.name}\n\n1. Generated by ReEvo",
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
            logger.warning(f"[ReEvo] Skipping candidate for operator {operator}: {exc}")
            return None

    async def _batch_evaluate(self, algorithms: list[Algorithm]) -> list[Algorithm]:
        """Batch-evaluate candidates via a single dispatcher call."""
        to_evaluate = [a for a in algorithms if not a.is_evaluated() and a.worktree is not None]
        if not to_evaluate:
            return algorithms

        eval_start = time.time()
        results = await self.dispatcher.dispatch_batch(algorithms=to_evaluate)
        self.state_tracker.record_timing("evaluate_batch", (time.time() - eval_start) * 1000)

        for alg, result in zip(to_evaluate, results, strict=True):
            self._apply_eval_result(alg, result)
            if self.state_tracker.generated_dir:
                alg.write(self.state_tracker.generated_dir, "evaluation", island_id=None, generation=alg.generation)
        return algorithms

    def _apply_eval_result(self, algorithm: Algorithm, result: EvaluationResult) -> None:
        """Apply an evaluation result to an algorithm and update counters."""
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
        else:
            self._eval_failures += 1
            logger.warning(
                f"[ReEvo] Evaluation failed for {algorithm.id}: {result.error_message} (score={result.score})"
            )

    # ------------------------------------------------------------------ #
    # Checkpoint / status / logging
    # ------------------------------------------------------------------ #

    async def pause(self) -> None:
        """Pause the ReEvo run."""
        self.state = EvolutionState.PAUSED

    async def resume(self) -> None:
        """Resume a paused ReEvo run."""
        if self.state == EvolutionState.PAUSED:
            self.state = EvolutionState.RUNNING

    async def save_checkpoint(self, path: str | None = None) -> str:
        """Persist a lightweight ReEvo checkpoint."""
        checkpoint = EvolutionCheckpoint(
            generation=self.current_generation,
            population=self.reevo_population.population,
            best_individual=self.reevo_population.best(),
            history=self.history,
            metadata={
                "total_samples": self.total_samples,
                "pop_size": self.reevo_population.pop_size,
                "long_term_reflection": self._long_term_reflection,
                "short_term_reflections": self._short_term_reflections,
            },
        )
        checkpoint_dir = getattr(self.config, "checkpoint_dir", None) or "."
        checkpoint_path = Path(path or (Path(checkpoint_dir) / f"reevo_{self.current_generation}.json"))
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
        return str(checkpoint_path)

    async def load_checkpoint(self, path: str) -> EvolutionCheckpoint:
        """Load a ReEvo checkpoint."""
        checkpoint = EvolutionCheckpoint.model_validate_json(Path(path).read_text(encoding="utf-8"))
        self.reevo_population.population = list(checkpoint.population)
        self.reevo_population.generation = checkpoint.generation
        self.best_individual = checkpoint.best_individual
        self.history = checkpoint.history
        self.total_samples = checkpoint.metadata.get("total_samples", 0)
        self._long_term_reflection = checkpoint.metadata.get("long_term_reflection", "")
        self._short_term_reflections = checkpoint.metadata.get("short_term_reflections", [])
        return checkpoint

    def get_status(self) -> dict[str, Any]:
        """Return current ReEvo status."""
        best = self.reevo_population.best()
        return {
            "state": self.state.value,
            "current_generation": self.current_generation,
            "total_samples": self.total_samples,
            "population_size": len(self.reevo_population.population),
            "best_score": best.score if best else None,
            **self.state_tracker.get_status_summary(),
        }

    async def _load_seed_population(self, seed_path: str) -> None:
        """Load seed algorithms from a JSON file."""
        raw = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        payload = raw if isinstance(raw, list) else raw.get("algorithms", [])
        algorithms = [Algorithm.model_validate(item) for item in payload]
        self.reevo_population.population = algorithms[: self.config.pop_size]

    def _log_generation_stats(self) -> None:
        """Record and log statistics for the current generation."""
        evaluated = [a for a in self.reevo_population.population if a.is_evaluated()]
        if evaluated:
            scores = [a.score for a in evaluated]
            best_score = max(scores)
            average_score = sum(scores) / len(scores)
            worst_score = min(scores)
        else:
            best_score = float("-inf")
            average_score = 0.0
            worst_score = float("inf")

        best = self.reevo_population.best()
        self.best_individual = best

        stats = {
            "generation": self.current_generation,
            "best_score": best.score if best else None,
            "best_name": best.name if best else None,
            "average_score": average_score,
            "population_size": len(self.reevo_population.population),
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
            f"[ReEvo] gen={self.current_generation} pop={len(self.reevo_population.population)} "
            f"best={best_score:.4f} avg={average_score:.4f} samples={self.total_samples} "
            f"eval_ok={self._eval_successes} eval_fail={self._eval_failures} "
            f"lt_reflection_len={len(self._long_term_reflection)}"
        )

    def _log_timing_summary(self) -> None:
        """Log a formatted table of accumulated module timing statistics."""
        module_timing = self.state_tracker.module_timing
        if not module_timing:
            return
        header = f"{'Module':<26} {'Calls':>6} {'Avg':>12} {'Total':>12}"
        sep = "-" * len(header)
        lines = ["", "[ReEvo] Timing Summary:", sep, header, sep]
        for key, mt in module_timing.items():
            lines.append(
                f"{key:<26} {mt.call_count:>6} "
                f"{format_duration_ms(mt.average_time_ms):>12} "
                f"{format_duration_ms(mt.total_time_ms):>12}"
            )
        lines.append(sep)
        logger.info("\n".join(lines))
