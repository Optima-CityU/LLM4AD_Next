"""Default LLM-based evolutionary planner implementation.

This is the default planner that coordinates the evolution process using:
- Multiple specialized samplers (init/mutate/crossover)
- SamplerSelector for choosing samplers with dynamic weight adjustment
- Supports memory-based retrieval of past good solutions for inspiration.
"""
import time
from pathlib import Path

from loguru import logger

from llm4ad.coder.base import BaseCoder
from llm4ad.config.schema import PlannerConfig
from llm4ad.infra.provider import BaseProvider
from llm4ad.infra.repo_analyzer import AnalyzedRepository
from llm4ad.infra.state import StateTracker
from llm4ad.infra.version_control import BaseVersionControl, WorktreeInfo
from llm4ad.planner.base import Algorithm, BasePlanner, InsightType
from llm4ad.planner.memory import Memory
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.prompt_templates import (
    IMPLEMENT_ALGORITHM,
    IMPLEMENT_ALGORITHM_PLAIN_LLM,
)
from llm4ad.planner.selector.sampler_selector import SamplerSelector


@BasePlanner.register("llm_evolution")
class LLMEvolutionPlanner(BasePlanner):
    """Default LLM-based evolutionary planner.

    Coordinates the evolution process by:
    - Using multiple specialized samplers (init/mutate/crossover)
    - Using SamplerSelector for dynamic sampler selection based on weights
    - Updating sampler weights based on algorithm fitness
    - Supports memory-based retrieval of past good solutions for inspiration.
    """

    def __init__(
        self,
        provider: BaseProvider,
        coder: BaseCoder,
        memory: Memory,
        config: PlannerConfig,
        analyzed_repository: AnalyzedRepository,
        version_control: BaseVersionControl,
        state_tracker: StateTracker
    ):
        """Initialize LLM Evolution planner.

        Args:
            provider: LLM provider for generating insights
            coder: Code generator for creating and modifying code
            memory: Memory system for storing/retrieving past experiences
            config: Planner configuration (supports both legacy and new formats):
                - Legacy format: init_sampler_type, mutate_sampler_type, crossover_sampler_type
                - New format: planner.samplers list with SamplerConfig
            analyzed_repository: Pre-analyzed repository with EVOLVE blocks
            version_control: Version control system for managing worktrees
            state_tracker: State tracker for tracking evolution progress and workspace paths
        """
        super().__init__(
            provider, coder, memory, config, analyzed_repository, version_control, state_tracker
        )

        # Initialize samplers
        self.samplers: list[BaseSampler] = []
        self.sampler_map: dict[str, BaseSampler] = {}  # name -> sampler instance
        self.sampler_selector: SamplerSelector | None = None

        # Parse config and initialize samplers
        self._init_samplers(config, provider, memory, analyzed_repository)

    def _init_samplers(
        self,
        config: PlannerConfig,
        provider: BaseProvider,
        memory: Memory,
        analyzed_repository: AnalyzedRepository,
    ) -> None:
        """Initialize samplers from configuration.

        Supports two formats:
        1. New format: planner.samplers list with SamplerConfig
        2. Legacy format: init_sampler_type, mutate_sampler_type, crossover_sampler_type

        Args:
            config: Planner configuration
            provider: LLM provider
            memory: Memory system
            analyzed_repository: Analyzed repository
        """
        # Try new format first (planner.samplers)
        samplers_config = self.config.samplers
        selection_strategy = self.config.selection_strategy

        for sampler_cfg in samplers_config:
            name = sampler_cfg.name
            sampler_config = sampler_cfg.config

            if name:
                try:
                    sampler = BaseSampler.create(
                        name,
                        provider=provider,
                        memory=memory,
                        config=sampler_config,  # Merge configs
                        analyzed_repository=analyzed_repository,
                    )
                    self.samplers.append(sampler)
                    self.sampler_map[name] = sampler
                    logger.info(f"Initialized sampler: {name}")
                except Exception as e:
                    logger.warning(f"Failed to create sampler {name}: {e}")

        # Create sampler selector
        if self.samplers:
            self.sampler_selector = SamplerSelector(
                samplers=self.samplers,
                config={"selection_strategy": selection_strategy},
            )
            logger.info(f"Created SamplerSelector with strategy: {selection_strategy}")

        # Raise exception if no samplers provided
        if not self.samplers:
            logger.error("No samplers provided!")
            raise ValueError("No samplers provided!")

    async def init(
        self,
        island_id: str,
        generation_id: int,
        algorithm_id: str,
        **kwargs,
    ) -> WorktreeInfo | None:
        """Initialize the planner for a new island.

        This is called by the orchestrator when a new island is created. It can be used to
        perform any necessary setup before planning starts, such as initializing memory or
        seeding the initial population.

        Args:
            island_id: Unique identifier for the island
            generation_id: Initial generation number (should be 0)
            algorithm_id: Unique identifier for the initial algorithm
            **kwargs: Additional parameters
        """
        worktree_name = f"island_{island_id}_gen_{generation_id}_ind_{algorithm_id}"
        logger.info(f"Creating worktree for island {island_id} ({worktree_name}) ...")
        version_control_result = self.version_control.create_worktree(
            name=worktree_name,
        )

        if not version_control_result.success or not version_control_result.data:
            logger.warning(
                f"Failed to create worktree for island {island_id} ({worktree_name}): "
                f"{version_control_result.message}"
                f"error_detail: {version_control_result.error}"
            )
            return None

        worktree: WorktreeInfo = version_control_result.data.get("worktree")
        logger.info(f"Created worktree for island {island_id} ({worktree_name}): {worktree.path}")

        return worktree

    async def plan(
        self,
        population: list[Algorithm],
        generation: int,
        **kwargs,
    ) -> Algorithm:
        """Plan the next algorithm individual in the evolution process.

        Main entry point called by the orchestrator. Uses SamplerSelector to
        dynamically choose which sampler to use based on weights.

        Args:
            population: Current population of algorithms
            generation: Current generation number
            **kwargs: Additional planning parameters including:
                - analyzed_repository: AnalyzedRepository from repository analyzer
                  (required for init_sampler)
                - background: Problem background description

        Returns:
            New algorithm individual with insight description
        """
        start_time = time.time()

        # For an empty population (generation 0), use init sampler
        if len(population) == 0 or generation == 0:
            # Find init sampler
            init_sampler = self.sampler_map.get("init_sampler")
            if init_sampler is None and self.samplers:
                # Use first sampler as fallback
                init_sampler = self.samplers[0]
            if init_sampler is None:
                raise ValueError("No init_sampler available for initial generation")
            algorithm = await init_sampler.sample(parents=population, generation=generation, **kwargs)
        else:
            # Use SamplerSelector to choose a sampler for evolution
            if self.sampler_selector is None or not self.samplers:
                raise ValueError("No samplers or sampler selector available for evolution")

            # Select a sampler using the selector
            selected_samplers = self.sampler_selector.select(n=1, population=population)
            sampler = selected_samplers[0]

            logger.info(f"Selected sampler for generation {generation}: {sampler.name}")

            # Prepare parents for the sampler based on its n_parents requirement
            parents = self.select_parents(population, sampler.n_parents, self.config.parent_selection_strategy)
            algorithm = await sampler.sample(parents=parents, generation=generation, **kwargs)

        # Record plan timing
        plan_time_ms = (time.time() - start_time) * 1000
        self.state_tracker.record_timing("planner.plan", plan_time_ms)
        if algorithm.timing.llm_planning_ms > 0:
            self.state_tracker.record_timing("provider.chat.planner", algorithm.timing.llm_planning_ms)

        # Log token usage from planner (sampler)
        if algorithm and algorithm.generation_meta and algorithm.generation_meta.tokens_used > 0:
            logger.info(
                f"Planner token usage for {algorithm.name}: "
                f"{algorithm.generation_meta.tokens_used} tokens "
                f"({algorithm.generation_meta.llm_model})"
            )

        return algorithm

    def update_sampler_weight(self, sampler: BaseSampler, fitness: float) -> None:
        """Update the weight of a sampler based on the fitness of generated algorithm.

        This is called after algorithm evaluation to adjust sampler weights
        for future selection.

        Args:
            sampler: The sampler that generated the algorithm
            fitness: The fitness value of the generated algorithm
        """
        if self.sampler_selector:
            self.sampler_selector.update_weight(sampler, fitness)
            logger.debug(
                f"Updated sampler {sampler.__class__.__name__} weight with fitness {fitness:.4f}"
            )

    async def implement(self, algorithm: Algorithm, **kwargs) -> Algorithm:
        """Implement the algorithm description into executable code.

        Uses the provided coder to generate code based on the algorithm description,
        then extracts the generated code into CodeArtifacts.

        Args:
            algorithm: Algorithm with description to implement
            **kwargs: Additional parameters:
                - task_description: Background description of the problem/task
                - base_context: Optional base context for generation

        Returns:
            Algorithm with code artifacts populated
        """
        if algorithm.worktree is None:
            raise ValueError("Algorithm must have an associated worktree to implement code")

        working_dir: str = algorithm.worktree.path
        task_description: str = kwargs.get("task_description", "")

        # Choose the appropriate prompt template based on coder type.
        # Agent-based coders (claude_code) use the agent template which expects
        # the coder to handle file I/O via tools.
        # Plain LLM coders (custom) use the plain template which instructs
        # the LLM to return code in named markdown blocks.
        if self.coder.agent_type.value == "custom":
            from llm4ad.coder.custom_naive_coder import CustomNaiveCoder

            assert isinstance(self.coder, CustomNaiveCoder)
            if isinstance(self.config, dict):
                mask_evolve = self.config.get("mask_evolve_blocks", False)
            else:
                mask_evolve = self.config.mask_evolve_blocks
            project_context = self.coder._collect_project_context(
                working_dir, mask_evolve_blocks=mask_evolve,
            )
            prompt = IMPLEMENT_ALGORITHM_PLAIN_LLM.format(
                background=task_description,
                description=algorithm.description,
                project_context=project_context,
            )
        else:
            prompt = IMPLEMENT_ALGORITHM.format(
                background=task_description,
                description=algorithm.description,
            )
        logger.debug(f"Prompt for code generation: {len(prompt)} chars")

        # Prepare context
        context = self.coder.prepare_context(algorithm.description, kwargs.get("base_context", {}))
        context["insight_type"] = algorithm.insight_type.value
        context["domain"] = algorithm.domain
        if algorithm.name:
            context["algorithm_name"] = algorithm.name
        targeted_files = (
            list(algorithm.generation_meta.targeted_files)
            if algorithm.generation_meta
            else []
        )
        if targeted_files:
            context["targeted_files"] = targeted_files
            context.setdefault("main_file", targeted_files[0])
        replacement_code = algorithm.custom_metadata.get("unified_code")
        if self.coder.agent_type.value == "custom" and replacement_code:
            context["replacement_code"] = replacement_code

        target_snapshots: dict[str, bytes] = {}
        working_root = Path(working_dir).resolve()
        for target in targeted_files:
            target_path = (working_root / target).resolve()
            try:
                target_path.relative_to(working_root)
            except ValueError:
                continue
            if target_path.is_file():
                target_snapshots[target] = target_path.read_bytes()

        start_time = time.time()

        # Generate code
        log_file = (
            self.state_tracker.coder_dir / f"{algorithm.id}.log"
            if self.state_tracker.coder_dir
            else None
        )
        generate_result = await self.coder.generate(
            prompt=prompt,
            context=context,
            working_dir=working_dir,
            log_file=log_file,
        )

        if not generate_result.is_success:
            error_msg = generate_result.error_message or "Code generation failed"
            raise RuntimeError(f"Implementation failed: {error_msg}")
        if targeted_files:
            changed_targets = []
            for target in targeted_files:
                target_path = (working_root / target).resolve()
                try:
                    target_path.relative_to(working_root)
                except ValueError:
                    continue
                before = target_snapshots.get(target)
                if target_path.is_file() and before is not None and target_path.read_bytes() != before:
                    changed_targets.append(target)
            if not changed_targets:
                raise RuntimeError(
                    "Implementation did not modify any targeted file: "
                    + ", ".join(targeted_files)
                )

        # Log token usage from code generation
        tokens_used = generate_result.token_used
        if tokens_used > 0:
            logger.info(
                f"💰 {self.coder.name} token usage for {algorithm.name}: {tokens_used} tokens"
            )
        # Update token usage in generation metadata
        if algorithm.generation_meta:
            algorithm.generation_meta.tokens_used += tokens_used

        algorithm.update_timing(generate_result.timing)
        self.state_tracker.record_timing("planner.implement", (time.time() - start_time) * 1000)
        if generate_result.timing.llm_coding_ms > 0:
            self.state_tracker.record_timing("provider.chat.coder", generate_result.timing.llm_coding_ms)
        logger.info(f"Algorithm {algorithm.name} implement success via ({self.coder.name})!")

        return algorithm

    async def mutate(
        self,
        parent: Algorithm,
        insight: str,
        **kwargs,
    ) -> Algorithm:
        """Mutate an existing parent algorithm.

        Creates a new algorithm by applying the mutation insight to the parent.

        Args:
            parent: Parent algorithm to mutate
            insight: Natural language description of the mutation
            **kwargs: Additional parameters

        Returns:
            New mutated algorithm
        """
        algorithm = parent.model_copy(deep=True)
        algorithm.id = algorithm.id  # Keep the same ID for tracking
        algorithm.parent_ids = [parent.id]
        algorithm.generation = parent.generation + 1
        algorithm.insight_type = InsightType.MUTATION
        algorithm.description = insight

        # Inherit parent's code artifacts - coder will handle generating the new code
        return algorithm

    async def crossover(
        self,
        parents: list[Algorithm],
        insight: str,
        **kwargs,
    ) -> Algorithm:
        """Combine multiple parent algorithms into a child.

        Args:
            parents: Parent algorithms to combine
            insight: Natural language description of how to combine them
            **kwargs: Additional parameters

        Returns:
            New combined algorithm
        """
        child = parents[0].model_copy(deep=True)
        child.id = child.id  # Keep ID
        child.parent_ids = [p.id for p in parents]
        child.generation = max(p.generation for p in parents) + 1
        child.insight_type = InsightType.CROSSOVER
        child.description = insight

        # Inherit code artifacts from parents - coder handles combination
        return child
