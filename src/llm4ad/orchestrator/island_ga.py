"""Island Genetic Algorithm orchestrator for LLM4AD.

Implements a distributed evolutionary algorithm where populations are divided into
subpopulations (islands) that evolve independently, with periodic migration of
individuals between islands. This improves genetic diversity and enables parallel
execution of evolution workloads.
"""

import asyncio
import random
import time
import uuid
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from llm4ad.coder.base import BaseCoder
from llm4ad.config.evolution import IslandGAConfig, MigrationStrategy, MigrationTopology
from llm4ad.evaluator import EvaluationDispatcher, EvaluationResult
from llm4ad.infra.state import EvolutionState, GenerationMetrics, ResourceUsage, StateTracker
from llm4ad.infra.version_control.base import BaseVersionControl
from llm4ad.orchestrator.base import (
    BaseOrchestrator,
    EvolutionCheckpoint,
    EvolutionResult,
    format_duration_ms,
)
from llm4ad.orchestrator.embedding_client import EmbeddingClient
from llm4ad.planner.base import Algorithm, BasePlanner

# Optional psutil for resource tracking
try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False




class Island(BaseModel):
    """Represents a single island in the Island GA.

    Each island maintains its own independent population and evolution state.
    """

    island_id: int
    population: list[Algorithm] = Field(default_factory=list)
    best_individual: Algorithm | None = None
    current_generation: int = 0
    last_migration_generation: int = 0
    island_config: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get_migrants(self, count: int, strategy: MigrationStrategy) -> list[Algorithm]:
        """Get individuals to migrate from this island.

        Args:
            count: Number of migrants to select
            strategy: Selection strategy for migrants

        Returns:
            List of selected migrant individuals
        """
        if count <= 0 or not self.population:
            return []

        evaluated = [ind for ind in self.population if ind.is_evaluated()]
        if not evaluated:
            # If no evaluated individuals, select random
            return random.sample(self.population, min(count, len(self.population)))

        if strategy == MigrationStrategy.BEST:
            # Sort by score descending, take top N
            sorted_pop = sorted(evaluated, key=lambda x: x.score, reverse=True)
            return sorted_pop[: min(count, len(sorted_pop))]

        elif strategy == MigrationStrategy.RANDOM:
            return random.sample(evaluated, min(count, len(evaluated)))

        elif strategy == MigrationStrategy.ELITE:
            # Take top 10%
            elite_count = max(1, int(len(evaluated) * 0.1))
            sorted_pop = sorted(evaluated, key=lambda x: x.score, reverse=True)
            return sorted_pop[: min(count, elite_count, len(sorted_pop))]

        elif strategy == MigrationStrategy.WORST:
            sorted_pop = sorted(evaluated, key=lambda x: x.score)
            return sorted_pop[: min(count, len(sorted_pop))]

        else:
            raise ValueError(f"Unknown migration strategy: {strategy}")

    def receive_migrants(self, migrants: list[Algorithm], replace_worst: bool = True) -> None:
        """Receive migrants from other islands.

        Args:
            migrants: List of incoming migrant individuals
            replace_worst: If True, replace worst individuals with migrants;
                          if False, append migrants to population
        """
        if not migrants:
            return

        # Update island_id for all incoming migrants
        for migrant in migrants:
            migrant.island_id = self.island_id

        if replace_worst and len(self.population) >= self.island_config.get("population_size", 20):
            # Replace worst individuals in the population
            evaluated = [ind for ind in self.population if ind.is_evaluated()]
            if evaluated:
                # Sort by score ascending (worst first)
                sorted_pop = sorted(evaluated, key=lambda x: x.score)
                num_replace = min(len(migrants), len(sorted_pop))
                # Remove worst N individuals
                for i in range(num_replace):
                    self.population.remove(sorted_pop[i])
                # Add migrants
                self.population.extend(migrants[:num_replace])
            else:
                # No evaluated individuals, just append
                self.population.extend(migrants)
        else:
            self.population.extend(migrants)

        # Update best individual
        all_individuals = self.population + migrants
        evaluated = [ind for ind in all_individuals if ind.is_evaluated()]
        if evaluated:
            new_best = max(evaluated, key=lambda x: x.score)
            if self.best_individual is None or new_best.score > self.best_individual.score:
                self.best_individual = new_best


@BaseOrchestrator.register("island_ga")
class IslandGAOrchestrator(BaseOrchestrator):
    """Island Genetic Algorithm orchestrator.

    Implements a distributed evolutionary algorithm with multiple independent islands
    and periodic migration of individuals between islands.
    """

    def __init__(
            self,
            planner: BasePlanner,
            coder: BaseCoder,
            dispatcher: EvaluationDispatcher,
            monitor: Any,
            config: IslandGAConfig,
            version_control: BaseVersionControl,
            state_tracker: StateTracker,
            background: str = "",
            embedding_client: EmbeddingClient = None,
    ):
        """Initialize Island GA orchestrator.

        Args:
            planner: Planner for generating new algorithm insights
            coder: Coder for implementing algorithms from insights
            dispatcher: Dispatcher for scheduling evaluation tasks
            monitor: Monitor for tracking evolution progress
            config: Island GA configuration
            version_control: Optional version control instance for isolated workspaces
            state_tracker: Tracker for managing evolutionary state
            background: Problem background description from top-level config
            embedding_client: EmbeddingClient for embedding generation
        """
        super().__init__(planner, coder, dispatcher, monitor, config, state_tracker, background, embedding_client)
        self.config: IslandGAConfig = config
        self.version_control: BaseVersionControl = version_control

        # Island state
        self.islands: list[Island] = []
        self.global_best_individual: Algorithm | None = None
        self._prev_best_metrics: dict[str, float] = {}

        # Migration state
        self._last_migration_gen = 0

        # Token tracking per generation
        self._gen_tokens: int = 0
        self._gen_algo_count: int = 0

        # Concurrency limit for individual generation pipelines
        self._llm_semaphore: asyncio.Semaphore | None = None
        if config.max_llm_concurrency is not None:
            self._llm_semaphore = asyncio.Semaphore(config.max_llm_concurrency)
            logger.info(
                f"IslandGAOrchestrator: max_llm_concurrency={config.max_llm_concurrency}"
            )

    async def _limited_generate(
        self,
        parents: list[Algorithm],
        island_id: int,
        generation: int,
    ) -> Algorithm | None:
        """Wrap ``generate_new_individual`` with the optional LLM semaphore."""
        if self._llm_semaphore is not None:
            async with self._llm_semaphore:
                return await self.generate_new_individual(
                    parents, island_id, generation
                )
        return await self.generate_new_individual(parents, island_id, generation)

    async def _limited_coro(self, coro: Any) -> Any:
        """Wrap an arbitrary coroutine with the optional LLM semaphore."""
        if self._llm_semaphore is not None:
            async with self._llm_semaphore:
                return await coro
        return await coro

    async def generate_new_individual(
        self,
        parents: list[Algorithm],
        island_id: int,
        generation: int,
    ) -> Algorithm | None:
        """Generate a new individual for the given island.

        This method encapsulates the full process of creating a new algorithm

        Arguments:
            parents: List of parent algorithms to use for generating the new
                individual (can be empty for initial generation)
            island_id: ID of the island for which to generate the individual
            generation: ID of the generation for which to generate the individual

        Returns:
            The generated Algorithm individual, or None if generation failed
        """
        try:
            algorithm_id = uuid.uuid4().hex[:12]
            worktree_time = insight_time = implement_time = build_time = eval_time = 0
            # 1. Create worktree
            step_start = time.time()
            worktree = await self.planner.init(
                island_id=island_id,
                generation_id=self.current_generation,
                algorithm_id=algorithm_id,
            )
            worktree_time = (time.time() - step_start) * 1000
            self.state_tracker.record_timing("create_worktree", worktree_time)

            if not worktree:
                return None
            logger.info(f"  ⏱ Worktree created in {worktree_time:.0f}ms")

            # 2. Generate algorithm insight
            logger.debug("Generate algorithm insight using model: {}", self.planner.provider.model)
            step_start = time.time()
            algorithm: Algorithm = await self.planner.plan(
                population=parents, generation=generation,
                background=self.background,
            )
            insight_time = (time.time() - step_start) * 1000
            self.state_tracker.record_timing("generate_insight", insight_time)
            logger.info(f"  ⏱ Insight generated in {insight_time:.0f}ms")
            algorithm.island_id = island_id
            algorithm.id = algorithm_id

            logger.info(
                f"🧬 Selected {len(parents)} parent individuals for island {island_id}"
            )
            if parents:
                best_parent = max(parents, key=lambda x: x.score)
                logger.info(
                    f"   Best parent: {best_parent.name} (score: {best_parent.score:.4f})"
                )
            logger.info(
                f"✅ Generated algorithm insight ({algorithm_id}) for island {island_id}: "
                f"{algorithm.name}"
            )
            logger.debug(f"Algorithm insight: {algorithm.description}")

            algorithm.worktree = worktree

            # Export insight stage
            if self.state_tracker.generated_dir:
                algorithm.write(self.state_tracker.generated_dir,
                                stage="insight",
                                island_id=island_id,
                                generation=generation)
                logger.info("Successfully written to the Generated directory")

            # 3. Implement algorithm
            step_start = time.time()
            algorithm = await self.planner.implement(
                algorithm=algorithm, worktree=worktree,
                task_description=self.background,
            )
            implement_time = (time.time() - step_start) * 1000
            self.state_tracker.record_timing("implement_code", implement_time)
            logger.info(
                f"Implemented algorithm ({algorithm_id}) for island {island_id} "
                f"in generation {generation}: {algorithm.name} ({implement_time:.0f}ms)"
            )

            # 4. Build verification
            step_start = time.time()
            await self.planner.build(algorithm=algorithm, worktree=worktree)
            build_time = (time.time() - step_start) * 1000
            self.state_tracker.record_timing("build_algorithm", build_time)
            logger.info(f"  ⏱ Build verified in {build_time:.0f}ms")

            # 5. Commit algorithm
            self.version_control.commit_changes(
                worktree=worktree,
                message=f"feat: `{algorithm.name}` on island {island_id} "
                f"in generation {generation}\n\n{algorithm.description}",
            )

            # 6. Get changed files and construct CodeArtifacts
            changed_files = self.version_control.get_changed_files(
                commit_hash=worktree.commit_hash
            )
            for file_info in changed_files:
                algorithm.add_code_artifact(
                    file_path=file_info.get("file_path", ""),
                    content=file_info.get("content", ""),
                    content_mode="full",
                )

            # Populate diff-based workflow metadata
            algorithm.changed_files = [
                f.get("file_path", "") for f in changed_files
            ]
            diff_stats = self.version_control.get_diff_stats(
                commit_hash=worktree.commit_hash
            )
            algorithm.lines_added = diff_stats.get("additions", 0)
            algorithm.lines_removed = diff_stats.get("deletions", 0)

            # 5. Evaluate algorithm
            step_start = time.time()
            algorithm = await self._evaluate_algorithm(algorithm)
            eval_time = (time.time() - step_start) * 1000
            self.state_tracker.record_timing("evaluate_algorithm", eval_time)

            # Export evaluation stage
            if self.state_tracker.generated_dir:
                algorithm.write(self.state_tracker.generated_dir, stage="evaluation", island_id=island_id,
                                generation=generation)
                self._schedule_embedding_save(algorithm)

            # Log evaluation result
            if algorithm.is_evaluated():
                logger.info(
                    f"📈 Evaluated {algorithm.name}: score={algorithm.score:.4f} "
                    f"({eval_time:.0f}ms)"
                )
            else:
                logger.warning(
                    f"❌ Evaluation failed for {algorithm.name}: "
                    f"{algorithm.evaluation.error if algorithm.evaluation else 'Unknown error'}"
                )

            # Log total time for this individual
            total_time = worktree_time + insight_time + implement_time + build_time + eval_time
            logger.info(f"  ⏱ Total time for {algorithm.name}: {total_time:.0f}ms")

            # Accumulate token usage for this generation
            if algorithm.generation_meta and algorithm.generation_meta.tokens_used > 0:
                self._gen_tokens += algorithm.generation_meta.tokens_used
                self._gen_algo_count += 1

            return algorithm
        except Exception as e:
            logger.warning(
                f"Skipping individual for island {island_id} in generation "
                f"{generation}: {e}"
            )
            return None

    async def initialize_population(self) -> list[Algorithm]:
        """Initialize populations for all islands.

        Creates initial populations for each island, either randomly or from seeds.

        Returns:
            Combined global population from all islands
        """
        self.islands = []

        for island_id in range(self.config.num_islands):
            # Get island-specific config if available
            island_config = (
                self.config.per_island_config.get(island_id, {})
                if self.config.per_island_config
                else {}
            )
            island_pop_size = island_config.get(
                "population_size", self.config.island_population_size
            )

            # Initialize island population in parallel
            tasks = [
                self._limited_generate(parents=[], island_id=island_id, generation=0)
                for _ in range(island_pop_size)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            island_population: list[Algorithm] = [
                r for r in results
                if r is not None and not isinstance(r, BaseException)
            ]

            # Create island
            island = Island(
                island_id=island_id,
                population=island_population,
                current_generation=0,
                island_config=island_config,
            )

            # Set island best
            evaluated = [ind for ind in island_population if ind.is_evaluated()]
            if evaluated:
                island.best_individual = max(evaluated, key=lambda x: x.score)

            self.islands.append(island)

        # Update global best
        self._update_global_best()

        # Log initial population summary
        self._print_population_summary()

        # Return combined global population
        return self._get_global_population()

    async def run(self) -> EvolutionResult:
        """Run the complete Island GA evolution process.

        Executes the full evolution loop across all islands, handling migration
        at configured intervals.

        Returns:
            EvolutionResult with final state and best individual
        """
        self.state = EvolutionState.RUNNING
        self.start_time = time.time()
        self.current_generation = 0
        self.state_tracker.start_run(self.config.max_generations)

        try:
            # Initialize population if empty
            if not self.islands:
                await self.initialize_population()
                self.current_generation += 1
                # Save state after initialization
                self.state_tracker.save_to_cache("init_population")

            # Main evolution loop
            while (
                self.current_generation <= self.config.max_generations
                and self.state == EvolutionState.RUNNING
            ):
                # Progress: 显示当前进度
                logger.info(
                    "═══════════════════════════════════════════════════════════════"
                )
                logger.info(
                    f"📊 Generation {self.current_generation} / {self.config.max_generations} "
                    f"[{self.current_generation * 100 // self.config.max_generations}%]"
                )
                logger.info(
                    "═══════════════════════════════════════════════════════════════"
                )

                # Run one generation step
                should_continue, best = await self.step()

                # Save state after each generation
                self.state_tracker.save_to_cache("generation")

                # Log timing summary for this generation
                self._log_timing_summary()

                logger.info(
                    f"Completed generation {self.current_generation}. "
                    f"Best score so far: {best.score if best else 'N/A'}"
                )

                if not should_continue:
                    break

                self.current_generation += 1

                # Check if migration should occur
                if self._should_migrate():
                    await self._perform_migration()

                # Checkpoint if needed
                if self.should_checkpoint():
                    await self.save_checkpoint()
                    self._last_checkpoint_gen = self.current_generation

                # Check early stopping
                should_stop, reason = self.check_early_stop()
                if should_stop:
                    self.monitor.log_info(f"Early stopping: {reason}")
                    break

            self.state = EvolutionState.COMPLETED

        except Exception as e:
            self.state = EvolutionState.FAILED
            self.monitor.log_error(f"Evolution failed: {str(e)}")
            raise

        finally:
            duration = time.time() - self.start_time
            self.state_tracker.end_run(self.state)

        # Return final result with version control metadata
        metadata = {}
        if self.version_control is not None:
            metadata["version_control_worktrees"] = [
                wt.name for wt in self.version_control.list_worktrees()
            ]

        # Return final result
        return EvolutionResult(
            state=self.state,
            best_individual=self.global_best_individual,
            final_generation=self.current_generation,
            total_evaluations=self._get_total_evaluations(),
            history=self.history,
            duration_seconds=duration,
            metadata=metadata,
        )

    async def step(self) -> tuple[bool, Algorithm | None]:
        """Execute one evolution step (one generation) across all islands.

        Evolves each island's population independently for one generation.

        Returns:
            Tuple of (should_continue, best_individual)
        """
        if self.state != EvolutionState.RUNNING:
            logger.warning("Step called but evolution is not in RUNNING state")
            return False, self.global_best_individual

        # Reset per-generation token counters
        self._gen_tokens = 0
        self._gen_algo_count = 0

        # Evolve all islands
        if self.config.parallel_islands:
            # Evolve islands in parallel
            tasks = [self._evolve_island(island) for island in self.islands]
            await asyncio.gather(*tasks)
        else:
            # Evolve islands sequentially
            for island in self.islands:
                await self._evolve_island(island)

        # Update global best
        self._update_global_best()

        # Auto-extract memory cards from this generation's population
        all_algorithms = self._get_global_population()
        await self._extract_memory_cards(all_algorithms, self.background)

        # Log generation stats
        self._log_generation_stats()

        return True, self.global_best_individual

    async def evolve_generation(
        self, island_id: int, parent_population: list[Algorithm]
    ) -> list[Algorithm]:
        """Evolve a single generation for a population.

        Maintains a constant population size equal to island_population_size by:
        1. Selecting elites from the original population (guaranteed survival)
        2. Generating offspring from selected parents
        3. Offspring compete with non-elite parents for the remaining slots
        4. Back-filling from the original population if not enough candidates

        Args:
            island_id: ID of the island for which to evolve the generation
            parent_population: Parent population to evolve

        Returns:
            New population after evolution
        """
        target_size = self.config.island_population_size

        # Elitism: keep best individuals from the ORIGINAL population
        elites: list[Algorithm] = []
        elite_ids: set[str] = set()
        if self.config.elite_ratio > 0:
            num_elites = max(1, int(target_size * self.config.elite_ratio))
            evaluated_parents = [ind for ind in parent_population if ind.is_evaluated()]
            if evaluated_parents:
                sorted_parents = sorted(evaluated_parents, key=lambda x: x.score, reverse=True)
                elites = sorted_parents[:num_elites]
                elite_ids = {ind.id for ind in elites}

        # Selection: select parents for reproduction from the original population
        num_selected = max(1, int(len(parent_population) * (1 - self.config.elite_ratio)))
        selected_parents = self.planner.select_parents(parent_population, num_selected)

        # Generate offspring (same count as remaining slots)
        num_offspring = target_size - len(elites)
        tasks = [
            self._limited_generate(
                parents=selected_parents,
                island_id=island_id,
                generation=self.current_generation,
            )
            for _ in range(num_offspring)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        offspring: list[Algorithm] = [r for r in results if isinstance(r, Algorithm)]

        # Competition: offspring compete with non-elite parents for remaining slots
        remaining_slots = target_size - len(elites)
        non_elite_parents = [ind for ind in parent_population if ind.id not in elite_ids]
        competitors = offspring + non_elite_parents

        # Select survivors using the configured survival selection strategy
        survivors = self.planner.select_survivors(
            competitors, remaining_slots, self.config.survival_selection_strategy
        )

        # Clean up worktrees for eliminated individuals
        survivor_ids = {ind.id for ind in survivors} | elite_ids
        for ind in competitors:
            if ind.id not in survivor_ids and ind.worktree:
                self.version_control.delete_worktree(ind.worktree)

        new_population = elites + survivors

        # Back-fill if still under target (e.g. all offspring failed and few parents)
        if len(new_population) < target_size:
            deficit = target_size - len(new_population)
            existing_ids = {ind.id for ind in new_population}
            candidates = sorted(
                [ind for ind in parent_population if ind.id not in existing_ids and ind.is_evaluated()],
                key=lambda x: x.score,
                reverse=True,
            )
            candidates += [
                ind for ind in parent_population if ind.id not in existing_ids and not ind.is_evaluated()
            ]
            backfill = candidates[:deficit]
            new_population.extend(backfill)

            if backfill:
                logger.warning(
                    f"Island {island_id}: back-filled {len(backfill)} from previous generation"
                )

        # Sort by score descending
        new_population = sorted(new_population, key=lambda x: x.score, reverse=True)
        await self._finish_embedding_tasks()
        return new_population

    async def pause(self) -> None:
        """Pause evolution at the next generation boundary."""
        self.state = EvolutionState.PAUSED
        self.monitor.log_info("Evolution paused")

    async def resume(self) -> None:
        """Resume evolution from paused state."""
        if self.state == EvolutionState.PAUSED:
            self.state = EvolutionState.RUNNING
            self.monitor.log_info("Evolution resumed")

    async def save_checkpoint(self, path: str | None = None) -> str:
        """Save current IGA state to checkpoint.

        Args:
            path: Optional checkpoint path (default: auto-generated)

        Returns:
            Path to saved checkpoint
        """
        # Create checkpoint data
        checkpoint = EvolutionCheckpoint(
            generation=self.current_generation,
            population=self._get_global_population(),
            best_individual=self.global_best_individual,
            history=self.history,
            metadata={
                "islands": [island.model_dump() for island in self.islands],
                "last_migration_gen": self._last_migration_gen,
                "state_tracker": self.state_tracker.to_checkpoint(),
            },
        )

        # Save to disk (implementation depends on storage backend)
        if path is None:
            path = (
                f"{self.state_tracker.checkpoints_dir}/iga_checkpoint_gen_"
                f"{self.current_generation}_{uuid.uuid4().hex[:8]}.json"
            )

        # Write checkpoint
        import os

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding='UTF-8') as f:
            f.write(checkpoint.model_dump_json(indent=2))

        self.monitor.log_info(f"Checkpoint saved to {path}")
        return path

    async def load_checkpoint(self, path: str) -> EvolutionCheckpoint:
        """Load IGA state from checkpoint.

        Args:
            path: Path to checkpoint file

        Returns:
            Loaded checkpoint data
        """
        import json

        with open(path, encoding='UTF-8') as f:
            checkpoint_data = json.load(f)

        checkpoint = EvolutionCheckpoint(**checkpoint_data)

        # Restore islands
        self.islands = [
            Island(**island_data) for island_data in checkpoint.metadata.get("islands", [])
        ]
        self._last_migration_gen = checkpoint.metadata.get("last_migration_gen", 0)
        self.current_generation = checkpoint.generation
        self.global_best_individual = checkpoint.best_individual
        self.history = checkpoint.history

        # Restore state tracker from checkpoint if available
        if "state_tracker" in checkpoint.metadata:
            self.state_tracker = self.state_tracker.from_checkpoint(
                checkpoint.metadata["state_tracker"]
            )
            # Update orchestrator state to match restored state
            self.state = self.state_tracker.state

        self.monitor.log_info(
            f"Checkpoint loaded from {path}, generation {self.current_generation}"
        )
        return checkpoint

    def get_status(self) -> dict[str, Any]:
        """Get current evolution status.

        Returns:
            Dictionary with current status information
        """
        status = {
            "state": self.state.value,
            "current_generation": self.current_generation,
            "global_best_score": (
                self.global_best_individual.score if self.global_best_individual else None
            ),
            "num_islands": len(self.islands),
            "total_population": self._get_total_population_size(),
            "total_evaluations": self._get_total_evaluations(),
            "last_migration_gen": self._last_migration_gen,
            "islands": [
                {
                    "island_id": island.island_id,
                    "population_size": len(island.population),
                    "best_score": island.best_individual.score if island.best_individual else None,
                    "current_generation": island.current_generation,
                }
                for island in self.islands
            ],
        }
        # Add state tracker summary
        status.update(self.state_tracker.get_status_summary())
        return status

    # Internal methods

    async def _evolve_island(self, island: Island) -> None:
        """Evolve a single island for one generation.

        Args:
            island: Island to evolve
        """
        # Evolve the island's population
        new_population = await self.evolve_generation(
            island_id=island.island_id, parent_population=island.population
        )

        # Update island state
        island.population = new_population
        island.current_generation += 1

        # Update island best
        evaluated = [ind for ind in new_population if ind.is_evaluated()]
        if evaluated:
            island.best_individual = max(evaluated, key=lambda x: x.score)

    def _should_migrate(self) -> bool:
        """Check if migration should occur at current generation.

        Returns:
            True if migration should be performed
        """
        if self.config.migration_interval <= 0:
            return False
        return self.current_generation - self._last_migration_gen >= self.config.migration_interval

    async def _perform_migration(self) -> None:
        """Perform migration of individuals between islands."""
        start_time = time.time()
        self.monitor.log_info(f"Performing migration at generation {self.current_generation}")

        num_migrants_per_island = max(
            1, int(self.config.island_population_size * self.config.migration_rate)
        )

        # Collect migrants from all islands
        migrants: dict[int, list[Algorithm]] = {}
        for island in self.islands:
            island_migrants = island.get_migrants(
                num_migrants_per_island, self.config.migration_strategy
            )
            migrants[island.island_id] = island_migrants

        # Distribute migrants according to topology
        if self.config.migration_topology == MigrationTopology.RING:
            # Ring topology: each island sends to next island
            num_islands = len(self.islands)
            for i, island in enumerate(self.islands):
                source_island_id = self.islands[(i - 1) % num_islands].island_id
                incoming_migrants = migrants.get(source_island_id, [])
                island.receive_migrants(incoming_migrants)

        elif self.config.migration_topology == MigrationTopology.FULLY_CONNECTED:
            # Fully connected: shuffle all migrants and distribute evenly
            all_migrants = []
            for island_migrants in migrants.values():
                all_migrants.extend(island_migrants)

            random.shuffle(all_migrants)
            migrants_per_island = len(all_migrants) // len(self.islands)

            for i, island in enumerate(self.islands):
                start = i * migrants_per_island
                end = start + migrants_per_island
                incoming_migrants = all_migrants[start:end]
                island.receive_migrants(incoming_migrants)

        elif self.config.migration_topology == MigrationTopology.MESH:
            # Custom mesh topology (simplified: random neighbors)
            for island in self.islands:
                # Select random destination islands
                num_neighbors = min(2, len(self.islands) - 1)
                destination_ids = random.sample(
                    [
                        other.island_id
                        for other in self.islands
                        if other.island_id != island.island_id
                    ],
                    num_neighbors,
                )
                # Distribute migrants to neighbors
                island_migrants = migrants.get(island.island_id, [])
                if island_migrants:
                    migrants_per_dest = max(1, len(island_migrants) // num_neighbors)
                    for dest_id in destination_ids:
                        dest_island = next(isl for isl in self.islands if isl.island_id == dest_id)
                        dest_island.receive_migrants(island_migrants[:migrants_per_dest])
                        island_migrants = island_migrants[migrants_per_dest:]

        else:
            raise ValueError(f"Unsupported migration topology: {self.config.migration_topology}")

        self._last_migration_gen = self.current_generation
        self._update_global_best()

        # Record timing and increment migration count
        elapsed_ms = (time.time() - start_time) * 1000
        self.state_tracker.record_timing("migration", elapsed_ms)
        self.state_tracker.increment_migration_count()

        best_score = (
            self.global_best_individual.score
            if self.global_best_individual
            else "N/A"
        )
        self.monitor.log_info(
            f"Migration completed, global best score: {best_score}"
        )

    async def _evaluate_algorithm(self, algorithm: Algorithm) -> Algorithm:
        """Evaluate a single algorithm.

        When version control is enabled, the algorithm gets its own isolated worktree
        for code generation.

        Args:
            algorithm: Algorithm to evaluate

        Returns:
            Evaluated Algorithm with score
        """
        # Skip if already evaluated
        if algorithm.is_evaluated():
            return algorithm

        if algorithm.worktree is None:
            logger.warning(f"Algorithm {algorithm.name} has no associated worktree for evaluation")
            return algorithm

        # Evaluate the algorithm
        # dispatch_batch aggregates per-file results internally;
        # returns one result per algorithm
        results = await self.dispatcher.dispatch_batch(algorithms=[algorithm])

        if not results:
            raise RuntimeError("Evaluation failed.")

        result: EvaluationResult = results[0]

        if result.success:
            algorithm.set_evaluation_result(score=result.score, metrics=result.metrics)
            # Propagate behavior data from evaluation
            if result.behavior is not None:
                algorithm.behavior_data = [result.behavior]
            all_behavior = result.metadata.get("all_behavior")
            if all_behavior:
                algorithm.behavior_data = all_behavior
        else:
            logger.warning(
                f"Evaluation failed for {algorithm.name}: "
                f"{result.error_message or 'Unknown error'}"
            )
            self.version_control.delete_worktree(algorithm.worktree, force=True)

        return algorithm

    async def _evaluate_population(self, population: list[Algorithm]) -> list[Algorithm]:
        """Evaluate a population of algorithms.

        When parallel_eval is enabled, batches all algorithms into a single
        dispatch_batch() call. For Pattern A (subprocess) evaluators, this
        enables true multi-algorithm parallel evaluation via asyncio.gather()
        across all (algorithm × instance) pairs.

        Args:
            population: List of Algorithm to evaluate

        Returns:
            List of evaluated Algorithm with scores
        """
        start_time = time.time()

        # Filter out already evaluated individuals
        to_evaluate = [ind for ind in population if not ind.is_evaluated()]

        if not to_evaluate:
            return population

        if self.config.parallel_eval:
            # Batch all algorithms into one dispatch_batch() call.
            # The dispatcher runs all (algorithm, data_file) pairs via
            # asyncio.gather(), giving true parallelism for async evaluators.
            valid = [ind for ind in to_evaluate if ind.worktree is not None]
            skipped = [ind for ind in to_evaluate if ind.worktree is None]

            for ind in skipped:
                logger.warning(
                    f"Algorithm {ind.name} has no associated worktree for evaluation"
                )

            if valid:
                results = await self.dispatcher.dispatch_batch(algorithms=valid)

                for ind, result in zip(valid, results, strict=True):
                    eval_time_ms = result.duration_ms or 0.0
                    if result.success:
                        ind.set_evaluation_result(score=result.score, metrics=result.metrics)
                        # Propagate behavior data from evaluation
                        if result.behavior is not None:
                            ind.behavior_data = [result.behavior]
                        all_behavior = result.metadata.get("all_behavior")
                        if all_behavior:
                            ind.behavior_data = all_behavior
                    else:
                        self.version_control.delete_worktree(
                            ind.worktree, force=True,
                        )
                    self.state_tracker.record_timing(
                        "evaluator.evaluate_algorithm", eval_time_ms,
                    )
        else:
            for ind in to_evaluate:
                await self._evaluate_algorithm(ind)

        # Automatic cleanup of old resources if enabled
        if self.version_control is not None:
            vc_config = getattr(self.version_control, "config", None)
            if vc_config and getattr(vc_config, "auto_cleanup", False):
                cleanup_result = self.version_control.cleanup_old_resources()
                if not cleanup_result.success:
                    self.monitor.log_warning(
                        f"Version control cleanup failed: {cleanup_result.error}"
                    )
                elif cleanup_result.data.get("deleted_count", 0) > 0:
                    deleted_count = cleanup_result.data["deleted_count"]
                    self.monitor.log_info(
                        f"Version control cleaned up {deleted_count} old worktrees"
                    )

        # Record total evaluation time
        total_time_ms = (time.time() - start_time) * 1000
        self.state_tracker.record_timing("evaluator.evaluate_population", total_time_ms)

        return population

    def _get_global_population(self) -> list[Algorithm]:
        """Get combined population from all islands.

        Returns:
            List of all individuals across all islands
        """
        global_pop = []
        for island in self.islands:
            global_pop.extend(island.population)
        return global_pop

    def _update_global_best(self) -> None:
        """Update the global best individual across all islands."""
        all_bests = [
            island.best_individual for island in self.islands if island.best_individual is not None
        ]
        if all_bests:
            current_best = max(all_bests, key=lambda x: x.score)
            if (
                self.global_best_individual is None
                or current_best.score > self.global_best_individual.score
            ):
                self.global_best_individual = current_best

    def _get_total_population_size(self) -> int:
        """Get total number of individuals across all islands."""
        return sum(len(island.population) for island in self.islands)

    def _get_total_evaluations(self) -> int:
        """Get total number of evaluated individuals across all islands."""
        count = 0
        for island in self.islands:
            count += sum(1 for ind in island.population if ind.is_evaluated())
        return count

    def _aggregate_generation_tokens(self) -> int:
        """Get total tokens consumed in the current generation.

        Returns:
            Total tokens accumulated via generate_new_individual.
        """
        return self._gen_tokens

    def _log_token_summary(self) -> None:
        """Log token consumption summary for the current generation."""
        cumulative_tokens = sum(
            m.total_tokens for m in self.state_tracker.metrics_history
        )

        logger.info(
            f"Token Consumption (Generation {self.current_generation}): "
            f"{self._gen_tokens:,} tokens ({self._gen_algo_count} algorithms) | "
            f"Cumulative: {cumulative_tokens + self._gen_tokens:,} tokens"
        )

    # ------------------------------------------------------------------
    # Memory extraction
    # ------------------------------------------------------------------

    async def _extract_memory_cards(
        self, algorithms: list[Algorithm], background: str
    ) -> None:
        """Extract memory cards from evaluated algorithms across all islands.

        Checks each algorithm against good/bad thresholds and extracts
        insights via the MemoryExtractor.

        Args:
            algorithms: All algorithms from the current population.
            background: Problem background description.
        """
        memory = self.planner.memory
        if memory is None or memory.extractor is None:
            return

        extractor = memory.extractor
        extractor.reset_generation()

        coros = []
        for algo in algorithms:
            if algo.is_evaluated() and algo.score is not None:
                coros.append(
                    self._limited_coro(
                        extractor.extract_from_good(
                            algo, algorithms, self.current_generation, background
                        )
                    )
                )
                coros.append(
                    self._limited_coro(
                        extractor.extract_from_bad(
                            algo, algorithms, self.current_generation, background
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
                            algo, error_msg, self.current_generation, background
                        )
                    )
                )

        if not coros:
            return

        results = await asyncio.gather(*coros, return_exceptions=True)
        cards = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Memory extraction failed: {result}")
            elif result is not None:
                cards.append(result)
        if cards:
            add_cards = getattr(memory, "add_cards", None)
            if callable(add_cards):
                await add_cards(cards)
            else:
                for card in cards:
                    await memory.add_card(card)

    def _log_best_metrics_comparison(self) -> None:
        """Log per-metric score comparison between current and previous generation's best."""
        if not self.global_best_individual:
            return
        current_metrics = self.global_best_individual.metrics
        if not current_metrics:
            return

        current_score = self.global_best_individual.score
        prev_score = self._prev_best_metrics.get("_overall_score")

        logger.info("")
        if self._prev_best_metrics:
            logger.info(
                f"📊 Best Individual Metrics (Gen {self.current_generation} vs Gen {self.current_generation - 1}):"
            )
        else:
            logger.info(f"📊 Best Individual Metrics (Gen {self.current_generation}):")

        logger.info("-" * 60)
        for name, value in current_metrics.items():
            prev_val = self._prev_best_metrics.get(name)
            if prev_val is not None:
                delta = value - prev_val
                sign = "+" if delta >= 0 else ""
                logger.info(f"  {name:<28} {prev_val:>6.2f} → {value:>6.2f}  ({sign}{delta:.2f})")
            else:
                logger.info(f"  {name:<28}          {value:>6.2f}")

        if prev_score is not None:
            delta = current_score - prev_score
            sign = "+" if delta >= 0 else ""
            logger.info("-" * 60)
            logger.info(f"  {'Overall':<28} {prev_score:>6.2f} → {current_score:>6.2f}  ({sign}{delta:.2f})")
        else:
            logger.info("-" * 60)
            logger.info(f"  {'Overall':<28}          {current_score:>6.2f}")
        logger.info("")

        self._prev_best_metrics = {**current_metrics, "_overall_score": current_score}

    def _log_generation_stats(self) -> None:
        """Log statistics for the current generation."""
        stats = {
            "generation": self.current_generation,
            "global_best_score": (
                self.global_best_individual.score if self.global_best_individual else None
            ),
            "avg_score": self._get_average_score(),
            "total_population": self._get_total_population_size(),
            "total_evaluated": self._get_total_evaluations(),
        }
        self.history.append(stats)
        self.monitor.log_generation(stats)
        self._log_best_metrics_comparison()

    def _log_timing_summary(self) -> None:
        """Log timing summary for all modules in the current generation."""
        if not self.state_tracker.module_timing:
            return

        human = getattr(self.config, "human_readable_timing", True)

        def _fmt(ms: float) -> str:
            return format_duration_ms(ms) if human else f"{ms:.0f}ms"

        logger.info("")
        logger.info("⏱ Timing Summary:")
        logger.info("-" * 60)

        # Define readable names for module keys
        module_names = {
            "create_worktree": "Create Worktree",
            "generate_insight": "Generate Insight",
            "implement_code": "Implement Code",
            "build_algorithm": "Build Algorithm",
            "evaluate_algorithm": "Evaluate Algorithm",
            "migration": "Migration",
        }

        total_time = 0.0
        for key, timing in self.state_tracker.module_timing.items():
            name = module_names.get(key, key)
            total_time += timing.total_time_ms
            logger.info(
                f"  {name:<20} | {timing.call_count:>4} calls | "
                f"avg: {_fmt(timing.average_time_ms):>16} | "
                f"total: {_fmt(timing.total_time_ms):>16}"
            )

        logger.info("-" * 60)
        logger.info(
            f"  {'TOTAL':<20} | {'':>4}      | "
            f"{'':>22} | {_fmt(total_time):>16}"
        )
        logger.info("")

        # Log token consumption for this generation
        self._log_token_summary()

        # Update state tracker with generation metrics
        all_evaluated = []
        for island in self.islands:
            all_evaluated.extend([ind for ind in island.population if ind.is_evaluated()])

        if not all_evaluated:
            best_score = float("-inf")
            avg_score = 0.0
            worst_score = float("inf")
        else:
            scores = [ind.score for ind in all_evaluated]
            best_score = max(scores)
            avg_score = sum(scores) / len(scores)
            worst_score = min(scores)

        total_evals = self._get_total_evaluations()
        prev_total = self.state_tracker.total_evaluations
        new_evals = total_evals - prev_total

        success_count = sum(
            1
            for island in self.islands
            for ind in island.population
            if ind.is_evaluated() and not getattr(ind, "error", None)
        )
        fail_count = new_evals - success_count

        island_best_scores = {
            island.island_id: (
                island.best_individual.score if island.best_individual else float("-inf")
            )
            for island in self.islands
        }

        metrics = GenerationMetrics(
            generation=self.current_generation,
            best_score=best_score,
            average_score=avg_score,
            worst_score=worst_score,
            total_evaluations=new_evals,
            successful_evaluations=success_count,
            failed_evaluations=fail_count,
            island_best_scores=island_best_scores,
            total_tokens=self._aggregate_generation_tokens(),
        )

        self.state_tracker.update_generation(self.current_generation, metrics)

        # Print population summary
        self._print_population_summary()

        # Record resource usage if enabled and psutil is available
        if (
            self.state_tracker.config.enable_resource_tracking
            and PSUTIL_AVAILABLE
            and (
                not self.state_tracker.resource_history
                or (
                    time.time() - self.state_tracker.resource_history[-1].timestamp
                    >= self.state_tracker.config.resource_tracking_interval_seconds
                )
            )
        ):
            process = psutil.Process()
            memory_mb = process.memory_info().rss / 1024 / 1024
            cpu_percent = psutil.cpu_percent(interval=None)
            worktree_count = 0
            if self.version_control is not None:
                worktree_count = len(self.version_control.list_worktrees())
            active_tasks = len(asyncio.all_tasks())

            usage = ResourceUsage(
                memory_usage_mb=memory_mb,
                cpu_usage_percent=cpu_percent,
                worktree_count=worktree_count,
                active_tasks=active_tasks,
            )
            self.state_tracker.record_resource_usage(usage)

    def _print_population_summary(self) -> None:
        """Print a summary of the current population sorted by fitness."""
        # Collect all individuals from all islands
        all_individuals = []
        for island in self.islands:
            for ind in island.population:
                all_individuals.append(ind)

        if not all_individuals:
            logger.info("No individuals in population to display")
            return

        # Sort by score (fitness) descending, evaluated individuals first
        evaluated = [ind for ind in all_individuals if ind.is_evaluated()]
        not_evaluated = [ind for ind in all_individuals if not ind.is_evaluated()]
        sorted_individuals = sorted(evaluated, key=lambda x: x.score, reverse=True) + not_evaluated

        # Print header
        logger.info("")
        logger.info(f"=== Population Summary (Generation {self.current_generation}) ===")
        logger.info(
            f"{'ID':<14} {'Name':<20} {'Gen':<5} {'Island':<7} {'Fitness':<10} {'Evaluated':<10} {'Lines +/-':<12}"
        )
        logger.info("-" * 90)

        # Print each individual
        for ind in sorted_individuals:
            ind_id = ind.id[:12] if len(ind.id) > 12 else ind.id
            name = (ind.name[:18] if ind.name else "unnamed") if len(ind.name or "") <= 18 else ind.name[:15] + "..."
            gen = ind.generation
            island = ind.island_id if ind.island_id is not None else "-"
            fitness = f"{ind.score:.4f}" if ind.is_evaluated() else "N/A"
            evaluated_str = "Yes" if ind.is_evaluated() else "No"
            lines = f"+{ind.lines_added}/-{ind.lines_removed}" if ind.lines_added or ind.lines_removed else "-"

            logger.info(
                f"{ind_id:<14} {name:<20} {gen:<5} {island:<7} {fitness:<10} {evaluated_str:<10} {lines:<12}"
            )

        logger.info("-" * 90)
        logger.info(f"Total: {len(all_individuals)} | Evaluated: {len(evaluated)} | Not evaluated: {len(not_evaluated)}")
        logger.info("")

    def _get_average_score(self) -> float | None:
        """Get average score across all evaluated individuals."""
        all_evaluated = []
        for island in self.islands:
            all_evaluated.extend([ind for ind in island.population if ind.is_evaluated()])

        if not all_evaluated:
            return None

        return sum(ind.score for ind in all_evaluated) / len(all_evaluated)
