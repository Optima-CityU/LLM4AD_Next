"""MEoH orchestrator implementation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from llm4ad.coder.base import BaseCoder
from llm4ad.config.evolution import MEoHConfig
from llm4ad.evaluator import EvaluationDispatcher, EvaluationResult
from llm4ad.evaluator.base import MetricType
from llm4ad.infra.state import EvolutionState, GenerationMetrics, StateTracker
from llm4ad.infra.timing import ExecutionTiming
from llm4ad.infra.version_control.base import BaseVersionControl
from llm4ad.orchestrator.base import BaseOrchestrator, EvolutionCheckpoint, EvolutionResult, format_duration_ms
from llm4ad.orchestrator.embedding_client import EmbeddingClient
from llm4ad.orchestrator.meoh_population import MEoHPopulation
from llm4ad.planner.base import Algorithm, BasePlanner
from llm4ad.planner.meoh_evolution import MEoHEvolutionPlanner


@BaseOrchestrator.register("meoh")
class MEoHOrchestrator(BaseOrchestrator):
    """Minimal runnable MEoH orchestrator."""

    def __init__(
            self,
            planner: BasePlanner,
            coder: BaseCoder,
            dispatcher: EvaluationDispatcher,
            monitor: Any,
            config: MEoHConfig,
            version_control: BaseVersionControl,
            state_tracker: StateTracker,
            background: str = "",
            embedding_client: EmbeddingClient = None,
    ) -> None:
        """Initialize the MEoH orchestrator."""
        super().__init__(planner, coder, dispatcher, monitor, config, state_tracker, background, embedding_client)
        if not isinstance(planner, MEoHEvolutionPlanner):
            raise TypeError("MEoHOrchestrator requires a MEoHEvolutionPlanner")
        self.planner: MEoHEvolutionPlanner = planner
        self.config: MEoHConfig = config
        self.version_control = version_control
        metric_directions = {
            name: metric.type for name, metric in self.dispatcher.get_metric_definitions().items()
        }
        # Ensure all objective_metrics have a direction.
        # Synthetic metrics like candidate_runtime_ms are not in the evaluator
        # definition — default them to MINIMIZE (lower is better).
        for name in config.objective_metrics:
            if name not in metric_directions:
                metric_directions[name] = MetricType.MINIMIZE
        self.meoh_population = MEoHPopulation(
            pop_size=config.population_size,
            num_operators=len(self._enabled_operators()),
            objective_metrics=config.objective_metrics,
            metric_directions=metric_directions,
        )
        self._background = getattr(config, "background", "")
        self.total_samples = 0

        # Concurrency limit for individual generation pipelines
        self._llm_semaphore: asyncio.Semaphore | None = None
        if config.max_llm_concurrency is not None:
            self._llm_semaphore = asyncio.Semaphore(config.max_llm_concurrency)
            logger.info(
                "MEoHOrchestrator: max_llm_concurrency=%d",
                config.max_llm_concurrency,
            )

        # Runtime counters for diagnostics
        self._planner_calls = 0
        self._planner_successes = 0
        self._planner_failures = 0
        self._coder_calls = 0
        self._coder_successes = 0
        self._coder_failures = 0
        self._eval_successes = 0
        self._eval_failures = 0
        self._duplicates_rejected = 0
        self._unique_code_hashes: set[str] = set()
        # Per-operator counters: {operator: {"calls": int, "successes": int}}
        self._operator_stats: dict[str, dict[str, int]] = {}
        self._operator_cycle: int = 0

    @property  # type: ignore[override]
    def current_generation(self) -> int:  # type: ignore[override]
        """Single source of truth for the current generation counter."""
        return self.meoh_population.generation

    @current_generation.setter
    def current_generation(self, value: int) -> None:
        """Allow base-class assignments (e.g. ``__init__``) without error."""
        # Ignored — generation is owned by meoh_population.
        pass

    async def run(self) -> EvolutionResult:
        """Run MEoH until a termination condition is met."""
        self.state = EvolutionState.RUNNING
        self.start_time = time.time()
        self.state_tracker.start_run(self.config.max_generations)
        try:
            if not self.meoh_population.population:
                await self.initialize_population()
                self._save_generation_population()
            while self.state == EvolutionState.RUNNING:
                if self.current_generation >= self.config.max_generations:
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
            self._save_generation_population()
            self._log_timing_summary()
            self._save_timing_summary()
            self.state_tracker.end_run(self.state)

        return EvolutionResult(
            state=self.state,
            best_individual=self.best_individual,
            final_population=list(self.meoh_population.elitist_archive),
            final_generation=self.current_generation,
            total_evaluations=self.total_samples,
            history=self.history,
            duration_seconds=time.time() - self.start_time,
            metadata={
                "elitist_archive_size": len(self.meoh_population.elitist_archive),
                "code_generation_mode": self.config.code_generation_mode,
                "objective_metrics": self.config.objective_metrics,
                "per_objective_best": {
                    k: {"value": v[0], "algorithm_id": v[1]}
                    for k, v in getattr(self, "_per_objective_best", {}).items()
                },
                "elitist_archive": [
                    {
                        "id": a.id,
                        "name": a.name,
                        "score": a.score,
                        "objectives": self._algorithm_objective_dict(a),
                    }
                    for a in self.meoh_population.elitist_archive
                ],
            },
        )

    async def _limited_generate(
        self,
        parents: list[Algorithm],
        operator: str,
        generation: int,
    ) -> Algorithm | None:
        """Wrap ``generate_new_individual`` with the optional LLM semaphore."""
        if self._llm_semaphore is not None:
            async with self._llm_semaphore:
                return await self.generate_new_individual(
                    parents=parents, operator=operator, generation=generation,
                )
        return await self.generate_new_individual(
            parents=parents, operator=operator, generation=generation,
        )

    async def step(self) -> tuple[bool, Algorithm | None]:
        """Execute one survival-based MEoH evolution step.

        Pipeline: parallel generate → batch evaluate → sequential register.
        """
        if self.state != EvolutionState.RUNNING:
            return False, self.best_individual

        operators = self._enabled_operators()
        if not operators:
            return False, self.best_individual

        # Collect all (operator, parents) pairs for this step
        tasks_info: list[tuple[str, list[Algorithm]]] = []
        if self.config.batch_per_operator:
            operator = operators[self._operator_cycle % len(operators)]
            self._operator_cycle += 1
            for _ in range(self.meoh_population.pop_size):
                if self.total_samples + len(tasks_info) >= self.config.max_sample_nums:
                    break
                parents = self._select_parents_for_operator(operator)
                tasks_info.append((operator, parents))
        else:
            for operator in operators:
                for _ in range(max(1, self.config.num_samplers)):
                    if self.total_samples + len(tasks_info) >= self.config.max_sample_nums:
                        break
                    parents = self._select_parents_for_operator(operator)
                    tasks_info.append((operator, parents))

        if not tasks_info:
            return False, self._select_best_individual()

        # 1. Parallel generation (plan + implement + build, no evaluation)
        generation = self.meoh_population.generation
        t0 = time.time()
        results = await asyncio.gather(*[
            self._limited_generate(parents, op, generation)
            for op, parents in tasks_info
        ])
        self.state_tracker.record_timing("step_generation", (time.time() - t0) * 1000)

        # 2. Batch evaluation of all generated candidates
        candidates = [r for r in results if r is not None]
        if candidates:
            t0 = time.time()
            await self._batch_evaluate(candidates)
            self.state_tracker.record_timing("step_evaluation", (time.time() - t0) * 1000)

        # 3. Sequential registration (survival order matters)
        t0 = time.time()
        for (operator, _), algorithm in zip(tasks_info, results, strict=True):
            self.total_samples += 1
            if algorithm is None:
                continue
            is_dup = self.meoh_population.has_duplicate(algorithm)
            if is_dup:
                self._duplicates_rejected += 1
                code_sig = "\n".join(a.content for a in algorithm.code_artifacts)
                logger.info(
                    f"[MEoH] Duplicate rejected: id={algorithm.id} op={operator} "
                    f"artifacts={len(algorithm.code_artifacts)} code_len={len(code_sig)} "
                    f"evaluated={algorithm.is_evaluated()} "
                    f"(total_dup={self._duplicates_rejected})"
                )
            triggered, eliminated = self.meoh_population.register_individual(algorithm)
            self.population = list(self.meoh_population.population)
            if triggered:
                self._cleanup_eliminated_worktrees(eliminated)
                await self._on_survival_triggered()
        self.state_tracker.record_timing("step_registration", (time.time() - t0) * 1000)

        return True, self._select_best_individual()

    async def initialize_population(self) -> list[Algorithm]:
        """Initialize the first active population, optionally from seed.

        Pipeline: parallel generate → batch evaluate → sequential register.
        """
        if self.config.seed_path:
            await self._load_seed_population(self.config.seed_path)
            if self.meoh_population.population:
                self.population = list(self.meoh_population.population)
                self.best_individual = self._select_best_individual()
                return self.population

        init_target = self.meoh_population.pop_size
        init_count = 0
        generation = self.meoh_population.generation
        init_start = time.time()

        while init_count < init_target and self.total_samples < self.config.max_sample_nums:
            remaining = init_target - init_count
            budget = self.config.max_sample_nums - self.total_samples
            batch_size = min(remaining, budget)
            if batch_size <= 0:
                break

            # 1. Parallel generation
            t0 = time.time()
            results = await asyncio.gather(*[
                self._limited_generate([], "i1", generation)
                for _ in range(batch_size)
            ])
            self.state_tracker.record_timing("step_generation", (time.time() - t0) * 1000)

            # 2. Batch evaluation
            candidates = [r for r in results if r is not None]
            if candidates:
                t0 = time.time()
                await self._batch_evaluate(candidates)
                self.state_tracker.record_timing("step_evaluation", (time.time() - t0) * 1000)

            # 3. Sequential registration (init phase — directly into population)
            t0 = time.time()
            for algorithm in results:
                self.total_samples += 1
                if algorithm is None:
                    continue
                registered = self.meoh_population.register_init_individual(algorithm)
                if registered:
                    init_count += 1
                else:
                    self._duplicates_rejected += 1
                    code_sig = "\n".join(a.content for a in algorithm.code_artifacts)
                    logger.warning(
                        f"[MEoH] Duplicate rejected in init: id={algorithm.id} "
                        f"artifacts={len(algorithm.code_artifacts)} code_len={len(code_sig)} "
                        f"({init_count}/{init_target} registered, "
                        f"{self.total_samples} samples, total_dup={self._duplicates_rejected})"
                    )
            self.state_tracker.record_timing("step_registration", (time.time() - t0) * 1000)

        if init_count < init_target:
            logger.warning(
                f"Initialization incomplete: only {init_count}/{init_target} unique individuals "
                f"registered after {self.total_samples} samples"
            )

        self.state_tracker.record_timing("init_population", (time.time() - init_start) * 1000)
        self.population = list(self.meoh_population.population)
        self.best_individual = self._select_best_individual()
        return self.population

    async def evolve_generation(self, parent_population: list[Algorithm]) -> list[Algorithm]:
        """Compatibility wrapper around one MEoH step."""
        self.population = parent_population
        await self.step()
        await self._finish_embedding_tasks()
        return list(self.meoh_population.population)

    async def pause(self) -> None:
        """Pause the MEoH run."""
        self.state = EvolutionState.PAUSED

    async def resume(self) -> None:
        """Resume a paused MEoH run."""
        if self.state == EvolutionState.PAUSED:
            self.state = EvolutionState.RUNNING

    async def save_checkpoint(self, path: str | None = None) -> str:
        """Persist a lightweight MEoH checkpoint."""
        checkpoint = EvolutionCheckpoint(
            generation=self.current_generation,
            population=self.population,
            best_individual=self.best_individual,
            history=self.history,
            metadata={
                "meoh_population": self.meoh_population.model_dump(mode="json"),
                "total_samples": self.total_samples,
            },
        )
        checkpoint_path = Path(path or (Path(self.config.checkpoint_dir) / f"meoh_{self.current_generation}.json"))
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
        return str(checkpoint_path)

    async def load_checkpoint(self, path: str) -> EvolutionCheckpoint:
        """Load a MEoH checkpoint."""
        checkpoint = EvolutionCheckpoint.model_validate_json(Path(path).read_text(encoding="utf-8"))
        metadata = checkpoint.metadata
        if "meoh_population" in metadata:
            self.meoh_population = MEoHPopulation(**metadata["meoh_population"])
            self.population = list(self.meoh_population.population)
        self.current_generation = checkpoint.generation
        self.best_individual = checkpoint.best_individual
        self.history = checkpoint.history
        self.total_samples = metadata.get("total_samples", 0)
        return checkpoint

    def get_status(self) -> dict[str, Any]:
        """Return current MEoH status."""
        return {
            "state": self.state.value,
            "current_generation": self.current_generation,
            "total_samples": self.total_samples,
            "population_size": len(self.meoh_population.population),
            "elitist_archive_size": len(self.meoh_population.elitist_archive),
            "best_score": self.best_individual.score if self.best_individual else None,
            **self.state_tracker.get_status_summary(),
        }

    async def _limited_coro(self, coro: Any) -> Any:
        """Wrap an arbitrary coroutine with the optional LLM semaphore."""
        if self._llm_semaphore is not None:
            async with self._llm_semaphore:
                return await coro
        return await coro

    async def generate_new_individual(
        self,
        parents: list[Algorithm],
        operator: str,
        generation: int,
    ) -> Algorithm | None:
        """Generate one MEoH candidate via plan → implement → build → commit.

        Evaluation is NOT performed here — callers batch-evaluate afterwards.
        """
        candidate_start = time.time()
        if operator not in self._operator_stats:
            self._operator_stats[operator] = {"calls": 0, "successes": 0}
        self._operator_stats[operator]["calls"] += 1

        try:
            algorithm_id = uuid.uuid4().hex[:12]
            worktree_time = plan_time = implement_time = build_time = 0.0

            # 1. Create worktree
            step_start = time.time()
            worktree = await self.planner.init(
                island_id=0,
                generation_id=generation,
                algorithm_id=algorithm_id,
            )
            worktree_time = (time.time() - step_start) * 1000
            self.state_tracker.record_timing("create_worktree", worktree_time)
            if worktree is None:
                return None

            # 2. Plan (insight generation via operator sampler)
            self._planner_calls += 1
            try:
                step_start = time.time()
                algorithm = await self.planner.plan(
                    population=parents,
                    generation=generation,
                    operator=operator,
                    parents=parents,
                    background=self._background,
                )
                plan_time = (time.time() - step_start) * 1000
                self.state_tracker.record_timing("generate_insight", plan_time)
                self._planner_successes += 1
            except Exception as exc:
                self._planner_failures += 1
                raise RuntimeError(f"Planner failed: {exc}") from exc

            algorithm.id = algorithm_id
            algorithm.generation = generation
            algorithm.worktree = worktree
            algorithm.custom_metadata["meoh"] = {
                "operator": operator,
                "parent_ids": [parent.id for parent in parents],
            }

            desc_preview = (algorithm.description or "")[:150].replace("\n", " ")
            logger.info(
                f"[MEoH-Insight] id={algorithm_id} op={operator} "
                f"name=\"{algorithm.name}\" "
                f"insight_type={algorithm.insight_type.value} "
                f"desc=\"{desc_preview}...\""
            )

            if self.state_tracker.generated_dir:
                algorithm.write(self.state_tracker.generated_dir, "insight", island_id=None, generation=generation)
                logger.info("Successfully written to the Generated directory")
                self._schedule_embedding_save(algorithm)

            # 3. Implement (coder generates code from insight)
            self._coder_calls += 1
            try:
                step_start = time.time()
                algorithm = await self.planner.implement(
                    algorithm=algorithm,
                    worktree=worktree,
                    task_description=self._background,
                    base_context={"parent_code": parents[0] if parents else None} if parents else {},
                )
                implement_time = (time.time() - step_start) * 1000
                self.state_tracker.record_timing("implement_code", implement_time)
                self._coder_successes += 1
            except Exception as exc:
                self._coder_failures += 1
                raise RuntimeError(f"Coder failed: {exc}") from exc

            # 4. Build verification
            step_start = time.time()
            await self.planner.build(algorithm=algorithm, worktree=worktree)
            build_time = (time.time() - step_start) * 1000
            self.state_tracker.record_timing("build_algorithm", build_time)

            # 5. Commit
            commit_result = self.version_control.commit_changes(
                worktree=worktree,
                message=f"feat: meoh {operator} {algorithm.name}\n\n1. Generated by MEoH",
            )
            if not commit_result.success:
                raise RuntimeError(commit_result.error or commit_result.message)

            for file_info in self.version_control.get_changed_files(commit_hash=worktree.commit_hash):
                algorithm.add_code_artifact(
                    file_path=file_info.get("file_path", ""),
                    content=file_info.get("content", ""),
                    content_mode="full",
                )

            # Track code uniqueness
            code_sig = "\n".join(a.content for a in algorithm.code_artifacts)
            code_hash = hashlib.md5(code_sig.encode()).hexdigest()[:8]
            self._unique_code_hashes.add(code_hash)

            algorithm.timing.wall_time_ms = (time.time() - candidate_start) * 1000
            algorithm.timing.recompute_overhead()
            self.state_tracker.record_candidate_timing(algorithm.id, algorithm.timing)

            self._operator_stats[operator]["successes"] += 1

            total_time = worktree_time + plan_time + implement_time + build_time
            logger.info(
                f"[MEoH] Generated {algorithm.name} (id={algorithm_id} op={operator}) "
                f"in {total_time:.0f}ms "
                f"(wt={worktree_time:.0f} plan={plan_time:.0f} "
                f"impl={implement_time:.0f} build={build_time:.0f})"
            )

            return algorithm
        except Exception as exc:
            logger.warning(f"Skipping MEoH candidate for operator {operator}: {exc}")
            return None

    def _log_individual_summary(
        self,
        algorithm: Algorithm,
        operator: str,
        generation: int,
        code_hash: str,
    ) -> None:
        """Log a concise summary line for one newly generated individual."""
        score = algorithm.score if algorithm.score is not None else float("nan")
        obj_parts = []
        for m in self.config.objective_metrics:
            val = algorithm.metrics.get(m)
            obj_parts.append(f"{m}={val:.4f}" if val is not None else f"{m}=N/A")
        obj_str = ", ".join(obj_parts) if obj_parts else f"score={score:.4f}"

        evaluated_tag = "OK" if algorithm.is_evaluated() else "FAIL"
        parent_ids = list(algorithm.custom_metadata.get("meoh", {}).get("parent_ids", []))
        parent_str = ",".join(parent_ids) if parent_ids else "none"

        elapsed = time.time() - self.start_time if self.start_time else 0.0
        # Per-objective best values (each independently optimized)
        per_best = getattr(self, "_per_objective_best", {})
        best_str = ", ".join(f"{k}={v[0]:.4f}" for k, v in per_best.items()) if per_best else "N/A"

        # Per-operator stats string: e.g. "i1=3/5 e1=2/4 m1=1/1"
        op_parts = []
        for op, st in sorted(self._operator_stats.items()):
            op_parts.append(f"{op}={st['successes']}/{st['calls']}")
        op_str = " ".join(op_parts)

        logger.info(
            f"[MEoH] sample={self.total_samples + 1}/{self.config.max_sample_nums} "
            f"gen={generation} op={operator} id={algorithm.id} "
            f"code={code_hash} eval={evaluated_tag} | "
            f"{obj_str} | "
            f"parents=[{parent_str}] "
            f"pop={len(self.meoh_population.population)} "
            f"archive={len(self.meoh_population.elitist_archive)} "
            f"unique_codes={len(self._unique_code_hashes)} "
            f"best=[{best_str}] | "
            f"planner={self._planner_successes}/{self._planner_calls}"
            f"(fail={self._planner_failures}) "
            f"coder={self._coder_successes}/{self._coder_calls}"
            f"(fail={self._coder_failures}) "
            f"eval_ok={self._eval_successes} eval_fail={self._eval_failures} "
            f"dup_rejected={self._duplicates_rejected} | "
            f"ops: {op_str} | "
            f"elapsed={elapsed:.1f}s"
        )

    async def _evaluate_algorithm(self, algorithm: Algorithm) -> Algorithm:
        """Evaluate one algorithm via the dispatcher."""
        results = await self.dispatcher.dispatch_batch([algorithm])
        if not results:
            raise RuntimeError("MEoH evaluation failed with no results")
        result: EvaluationResult = results[0]

        if not result.success:
            # Leave the algorithm unevaluated so no placeholder score is
            # written; it is excluded from selection via is_evaluated().
            logger.warning(
                f"Evaluation failed for algorithm {algorithm.id}: {result.error_message}"
            )
            return algorithm

        # Only synthesize candidate_runtime_ms for successful evaluations.
        metrics = dict(result.metrics)
        if "candidate_runtime_ms" not in metrics:
            metrics["candidate_runtime_ms"] = metrics.get("execution_time_ms", result.duration_ms)

        algorithm.set_evaluation_result(
            score=result.score,
            metrics=metrics,
            error=None,
            evaluation_time_ms=result.duration_ms,
            timing=ExecutionTiming(
                evaluation_total_ms=result.duration_ms,
                candidate_runtime_ms=metrics.get("candidate_runtime_ms", 0.0),
            ),
        )
        algorithm.custom_metadata["evaluation_result"] = result.model_dump(mode="json")
        return algorithm

    def _apply_eval_result(self, algorithm: Algorithm, result: EvaluationResult) -> None:
        """Apply an evaluation result to an algorithm and update counters.

        On failure the algorithm is left unevaluated (``evaluation`` stays
        ``None``) instead of recording a placeholder ``0.0`` score, matching
        the IslandGA behaviour. Failed candidates are excluded from selection
        and Pareto ranking via ``is_evaluated()``.

        Args:
            algorithm: Algorithm to update.
            result: Dispatcher evaluation result.
        """
        if not result.success:
            self._eval_failures += 1
            logger.warning(
                f"Evaluation failed for algorithm {algorithm.id}: {result.error_message}"
            )
            return

        metrics = dict(result.metrics)
        if "candidate_runtime_ms" not in metrics:
            metrics["candidate_runtime_ms"] = metrics.get("execution_time_ms", result.duration_ms)

        algorithm.set_evaluation_result(
            score=result.score,
            metrics=metrics,
            error=None,
            evaluation_time_ms=result.duration_ms,
            timing=ExecutionTiming(
                evaluation_total_ms=result.duration_ms,
                candidate_runtime_ms=metrics.get("candidate_runtime_ms", 0.0),
            ),
        )
        algorithm.custom_metadata["evaluation_result"] = result.model_dump(mode="json")
        self._eval_successes += 1
        self._update_per_objective_best(algorithm)

    async def _batch_evaluate(self, algorithms: list[Algorithm]) -> list[Algorithm]:
        """Batch-evaluate algorithms via a single dispatch_batch() call.

        All (algorithm x data_file) pairs are submitted together, enabling
        true parallelism bounded by the dispatcher's ``max_workers`` semaphore.
        """
        to_evaluate = [a for a in algorithms if not a.is_evaluated() and a.worktree is not None]
        if not to_evaluate:
            return algorithms

        skipped = [a for a in algorithms if a.worktree is None and not a.is_evaluated()]
        for a in skipped:
            logger.warning(f"Algorithm {a.name} has no worktree for evaluation")

        eval_start = time.time()
        results = await self.dispatcher.dispatch_batch(algorithms=to_evaluate)
        eval_time = (time.time() - eval_start) * 1000
        self.state_tracker.record_timing("evaluate_batch", eval_time)

        for alg, result in zip(to_evaluate, results, strict=True):
            self._apply_eval_result(alg, result)
            if result.success and self.state_tracker.generated_dir:
                alg.write(self.state_tracker.generated_dir, "evaluation", island_id=None, generation=alg.generation)

        logger.info(
            f"[MEoH] Batch evaluated {len(to_evaluate)} algorithms in {eval_time:.0f}ms "
            f"(eval_ok={self._eval_successes} eval_fail={self._eval_failures})"
        )
        return algorithms

    async def _extract_memory_cards(
        self,
        algorithms: list[Algorithm],
        background: str,
    ) -> None:
        """Extract memory cards concurrently after survival."""
        memory = self.planner.memory
        if memory is None or memory.extractor is None:
            return

        config = getattr(memory, "config", None)
        auto_extraction: Any | None = None
        if isinstance(config, dict):
            auto_extraction = config.get("auto_extraction")
        elif config is not None:
            auto_extraction = getattr(config, "auto_extraction", None)

        if isinstance(auto_extraction, dict) and not auto_extraction.get("enabled", True):
            return
        if (
            auto_extraction is not None
            and not isinstance(auto_extraction, dict)
            and not getattr(auto_extraction, "enabled", True)
        ):
            return

        extractor = memory.extractor
        extractor.reset_generation()

        coros = []
        for algo in algorithms:
            if algo.is_evaluated() and algo.score is not None:
                coros.append(
                    self._limited_coro(
                        extractor.extract_from_good(
                            algo,
                            algorithms,
                            self.current_generation,
                            background,
                        )
                    )
                )
                coros.append(
                    self._limited_coro(
                        extractor.extract_from_bad(
                            algo,
                            algorithms,
                            self.current_generation,
                            background,
                        )
                    )
                )
            elif not algo.is_evaluated():
                error_msg = ""
                if algo.evaluation and algo.evaluation.error:
                    error_msg = algo.evaluation.error
                coros.append(
                    self._limited_coro(
                        extractor.extract_from_failure(
                            algo,
                            error_msg,
                            self.current_generation,
                            background,
                        )
                    )
                )

        if not coros:
            return

        t0 = time.time()
        results = await asyncio.gather(*coros, return_exceptions=True)
        self.state_tracker.record_timing("memory_extraction", (time.time() - t0) * 1000)
        cards = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Memory extraction failed: {result}")
            elif result is not None:
                cards.append(result)
        if cards:
            try:
                add_cards = getattr(memory, "add_cards", None)
                if callable(add_cards):
                    await add_cards(cards)
                else:
                    await asyncio.gather(
                        *(self._limited_coro(memory.add_card(card)) for card in cards)
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Memory insertion failed: {exc}")

    async def _load_seed_population(self, seed_path: str) -> None:
        """Load seed algorithms from a JSON file."""
        raw = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        payload = raw if isinstance(raw, list) else raw.get("algorithms", [])
        algorithms = [Algorithm.model_validate(item) for item in payload]
        self.meoh_population.population = algorithms[: self.config.population_size]
        self.meoh_population.elitist_archive = list(algorithms)

    def _enabled_operators(self) -> list[str]:
        operators = ["e1"]
        if self.config.use_e2_operator:
            operators.append("e2")
        if self.config.use_m1_operator:
            operators.append("m1")
        if self.config.use_m2_operator:
            operators.append("m2")
        return operators

    def _select_parents_for_operator(self, operator: str) -> list[Algorithm]:
        if operator in {"e1", "e2"}:
            return self.meoh_population.selection(self.config.selection_num)
        return self.meoh_population.selection(1)

    def _select_best_individual(self) -> Algorithm | None:
        """Select the best individual from archive or population.

        Uses the Pareto objective vector (direction-aware) to pick the
        individual with the highest sum of normalized objective values.
        Also records per-objective best values in ``self._per_objective_best``.
        """
        candidates = (
            self.meoh_population.elitist_archive
            or self.meoh_population.population
        )
        if not candidates:
            return None

        evaluated = [a for a in candidates if a.is_evaluated()]
        if not evaluated:
            return candidates[0]

        # Record per-objective best (respecting direction)
        directions = self.meoh_population.metric_directions
        self._per_objective_best: dict[str, tuple[float, str]] = {}
        for metric_name in self.config.objective_metrics:
            direction = directions.get(metric_name)
            values = []
            for a in evaluated:
                val = a.metrics.get(metric_name)
                if val is not None:
                    values.append((val, a.id))
            if not values:
                continue
            if direction == MetricType.MINIMIZE:
                best_val, best_id = min(values, key=lambda x: x[0])
            else:
                best_val, best_id = max(values, key=lambda x: x[0])
            self._per_objective_best[metric_name] = (best_val, best_id)

        def sort_key(algorithm: Algorithm) -> float:
            vec = self.meoh_population.get_objective_vector(algorithm)
            return vec[0] if vec and vec[0] != float("-inf") else float("-inf")

        return max(evaluated, key=sort_key)

    def _cleanup_eliminated_worktrees(self, eliminated: list[Algorithm]) -> None:
        """Delete worktrees for individuals eliminated during survival.

        Args:
            eliminated: Algorithms removed from the population.
        """
        cleaned = 0
        for ind in eliminated:
            if ind.worktree:
                try:
                    self.version_control.delete_worktree(ind.worktree)
                    cleaned += 1
                except Exception as exc:
                    logger.warning(
                        f"[MEoH] Failed to delete worktree for {ind.id}: {exc}"
                    )
        if cleaned:
            logger.info(f"[MEoH] Cleaned up {cleaned} worktrees from {len(eliminated)} eliminated individuals")

    async def _on_survival_triggered(self) -> None:
        """Post-survival bookkeeping: sync state, log stats, save snapshot."""
        self.population = list(self.meoh_population.population)
        await self._extract_memory_cards(self.population, self._background)
        logger.info(
            f"[MEoH] === SURVIVAL triggered === gen={self.current_generation} "
            f"pop_after={len(self.meoh_population.population)} "
            f"archive={len(self.meoh_population.elitist_archive)} "
            f"samples={self.total_samples} "
            f"hv={self.meoh_population.last_hv:.6f}"
        )
        self._log_generation_stats()
        self._log_timing_summary()

    def _log_generation_stats(self) -> None:
        """Record MEoH generation statistics and persist population snapshot."""
        evaluated = [algorithm for algorithm in self.meoh_population.population if algorithm.is_evaluated()]
        if evaluated:
            scores = [algorithm.score for algorithm in evaluated]
            best_score = max(scores)
            average_score = sum(scores) / len(scores)
            worst_score = min(scores)
        else:
            best_score = float("-inf")
            average_score = 0.0
            worst_score = float("inf")

        best = self._select_best_individual()
        self.best_individual = best
        generation_timing = ExecutionTiming()
        for algorithm in self.meoh_population.population + self.meoh_population.elitist_archive:
            if algorithm.generation == self.current_generation:
                generation_timing.add(algorithm.timing)
        generation_timing.recompute_overhead()

        stats = {
            "generation": self.current_generation,
            "best_score": best.score if best else None,
            "best_objectives": self._algorithm_objective_dict(best) if best else {},
            "average_score": average_score,
            "archive_size": len(self.meoh_population.elitist_archive),
            "hv": self.meoh_population.last_hv,
            "elitist_archive": [
                {
                    "id": a.id,
                    "name": a.name,
                    "score": a.score,
                    "objectives": self._algorithm_objective_dict(a),
                    "metrics": a.metrics,
                }
                for a in self.meoh_population.elitist_archive
            ],
            "population_size": len(self.meoh_population.population),
            "samples": self.total_samples,
        }
        self.history.append(stats)
        self.monitor.log_generation(stats)

        self._save_generation_population()

        metrics = GenerationMetrics(
            generation=self.current_generation,
            best_score=best_score,
            average_score=average_score,
            worst_score=worst_score,
            total_evaluations=self.total_samples,
            successful_evaluations=len(evaluated),
            failed_evaluations=max(0, self.total_samples - len(evaluated)),
            timing=generation_timing,
        )
        self.state_tracker.update_generation(self.current_generation, metrics)
        self.state_tracker.record_generation_timing(self.current_generation, metrics.timing)

    def _algorithm_objective_dict(self, algorithm: Algorithm) -> dict[str, float | None]:
        """Build an objective dict for JSON serialization.

        Returns raw metric values (not sign-flipped) keyed by objective name.
        """
        return {
            m: algorithm.metrics.get(m) for m in self.config.objective_metrics
        }

    def _update_per_objective_best(self, algorithm: Algorithm) -> None:
        """Incrementally update per-objective best with a newly evaluated individual."""
        if not hasattr(self, "_per_objective_best"):
            self._per_objective_best = {}
        directions = self.meoh_population.metric_directions
        for metric_name in self.config.objective_metrics:
            val = algorithm.metrics.get(metric_name)
            if val is None:
                continue
            direction = directions.get(metric_name, MetricType.MAXIMIZE)
            current = self._per_objective_best.get(metric_name)
            if (
                current is None
                or (direction == MetricType.MINIMIZE and val < current[0])
                or (direction == MetricType.MAXIMIZE and val > current[0])
            ):
                self._per_objective_best[metric_name] = (val, algorithm.id)

    def _log_timing_summary(self) -> None:
        """Log a formatted table of accumulated module timing statistics."""
        module_timing = self.state_tracker.module_timing
        if not module_timing:
            return

        display_names = {
            "create_worktree": "Create Worktree",
            "generate_insight": "Generate Insight (Plan)",
            "implement_code": "Implement Code",
            "build_algorithm": "Build Algorithm",
            "evaluate_batch": "Batch Evaluation",
            "memory_extraction": "Memory Extraction",
            "step_generation": "Step: Generation",
            "step_evaluation": "Step: Evaluation",
            "step_registration": "Step: Registration",
            "init_population": "Init Population",
        }

        header = f"{'Module':<26} {'Calls':>6} {'Avg':>12} {'Min':>12} {'Max':>12} {'Total':>12}"
        sep = "-" * len(header)
        lines = [
            "",
            "[MEoH] Timing Summary:",
            sep,
            header,
            sep,
        ]
        for key, mt in module_timing.items():
            name = display_names.get(key, key)
            lines.append(
                f"{name:<26} {mt.call_count:>6} "
                f"{format_duration_ms(mt.average_time_ms):>12} "
                f"{format_duration_ms(mt.min_time_ms):>12} "
                f"{format_duration_ms(mt.max_time_ms):>12} "
                f"{format_duration_ms(mt.total_time_ms):>12}"
            )
        lines.append(sep)
        logger.info("\n".join(lines))

    def _save_timing_summary(self) -> None:
        """Persist module_timing and run_timing to a JSON file."""
        output_dir = self.state_tracker.state_dir
        if output_dir is None:
            return

        module_timing = self.state_tracker.module_timing
        data: dict[str, Any] = {
            "module_timing": {
                key: mt.model_dump() if hasattr(mt, "model_dump") else {
                    "total_time_ms": mt.total_time_ms,
                    "call_count": mt.call_count,
                    "average_time_ms": mt.average_time_ms,
                    "min_time_ms": mt.min_time_ms,
                    "max_time_ms": mt.max_time_ms,
                }
                for key, mt in module_timing.items()
            },
            "run_timing": self.state_tracker.run_timing.model_dump()
            if hasattr(self.state_tracker, "run_timing") and self.state_tracker.run_timing
            else {},
            "total_samples": self.total_samples,
            "generations": self.current_generation,
        }
        timing_path = output_dir / "timing_summary.json"
        timing_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        logger.info(f"[MEoH] Timing summary saved to {timing_path}")

    def _save_generation_population(self) -> None:
        """Persist a JSON snapshot of the current population for this generation."""
        output_dir = self.state_tracker.state_dir
        if output_dir is None:
            return

        def _serialize_individual(a: Algorithm) -> dict:
            return {
                "id": a.id,
                "name": a.name,
                "generation": a.generation,
                "score": a.score,
                "objectives": self._algorithm_objective_dict(a),
                "metrics": a.metrics,
                "description": a.description,
                "code_artifacts": [
                    {"file_path": ca.file_path, "content": ca.content}
                    for ca in a.code_artifacts
                ],
                "meoh_metadata": a.custom_metadata.get("meoh", {}),
            }

        pop_dir = output_dir / "populations"
        pop_dir.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "generation": self.current_generation,
            "total_samples": self.total_samples,
            "objective_metrics": self.config.objective_metrics,
            "population": [
                _serialize_individual(a) for a in self.meoh_population.population
            ],
            "elitist_archive": [
                _serialize_individual(a) for a in self.meoh_population.elitist_archive
            ],
        }
        snapshot_path = pop_dir / f"generation_{self.current_generation:04d}.json"
        snapshot_path.write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(
            f"Saved generation {self.current_generation} population snapshot "
            f"({len(self.meoh_population.population)} individuals, "
            f"{len(self.meoh_population.elitist_archive)} in archive) "
            f"to {snapshot_path}"
        )
