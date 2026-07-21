"""EoH-specific planner implementation.

Standalone planner for EoH: dispatches the I1/E1/E2/M1/M2 operators to the
``eoh_*`` samplers and issues the LLM call. Code implementation and build
verification are inherited from ``LLMEvolutionPlanner``. EoH does not share
its planner or samplers with any other method.

Reference:
    Fei Liu et al. "Evolution of Heuristics: Towards Efficient Automatic
    Algorithm Design Using Large Language Model." ICML 2024.
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from llm4ad.coder.base import BaseCoder
from llm4ad.infra.provider import BaseProvider
from llm4ad.infra.repo_analyzer import AnalyzedRepository
from llm4ad.infra.state import StateTracker
from llm4ad.infra.version_control import BaseVersionControl
from llm4ad.planner.base import Algorithm, BasePlanner
from llm4ad.planner.llm_evolution import LLMEvolutionPlanner
from llm4ad.planner.memory import Memory
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.eoh_samplers import (  # noqa: F401
    EoHE1Sampler,
    EoHE2Sampler,
    EoHInitSampler,
    EoHM1Sampler,
    EoHM2Sampler,
)


@BasePlanner.register("eoh_evolution")
class EoHEvolutionPlanner(LLMEvolutionPlanner):
    """Planner with explicit EoH operator dispatch."""

    OPERATOR_TO_SAMPLER = {
        "i1": "eoh_init_sampler",
        "e1": "eoh_e1_sampler",
        "e2": "eoh_e2_sampler",
        "m1": "eoh_m1_sampler",
        "m2": "eoh_m2_sampler",
    }

    def __init__(
        self,
        provider: BaseProvider,
        coder: BaseCoder,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository,
        version_control: BaseVersionControl,
        state_tracker: StateTracker,
    ) -> None:
        """Initialize the EoH planner and its samplers.

        Args:
            provider: LLM provider used for planning.
            coder: Coder used by ``implement``.
            memory: Memory system.
            config: Planner configuration mapping.
            analyzed_repository: Pre-analyzed repository with EVOLVE blocks.
            version_control: Worktree manager.
            state_tracker: Run state / timing tracker.
        """
        BasePlanner.__init__(
            self,
            provider=provider,
            coder=coder,
            memory=memory,
            config=config,
            analyzed_repository=analyzed_repository,
            version_control=version_control,
            state_tracker=state_tracker,
        )
        self.samplers: list[BaseSampler] = []
        self.sampler_map: dict[str, BaseSampler] = {}
        for sampler_name in self.OPERATOR_TO_SAMPLER.values():
            sampler = BaseSampler.create(
                sampler_name,
                provider=provider,
                memory=memory,
                config=config,
                analyzed_repository=analyzed_repository,
            )
            self.samplers.append(sampler)
            self.sampler_map[sampler_name] = sampler

    async def init(
        self,
        island_id: str,
        generation_id: int,
        algorithm_id: str,
        **kwargs: Any,
    ):
        """Create an EoH worktree.

        Args:
            island_id: Unused (EoH has a single population).
            generation_id: Current generation number.
            algorithm_id: Unique id for the individual.
            **kwargs: Ignored.

        Returns:
            The created worktree info, or None on failure.
        """
        worktree_name = f"eoh_gen_{generation_id}_ind_{algorithm_id}"
        logger.info(f"Creating worktree for EoH generation {generation_id} ({worktree_name}) ...")
        version_control_result = self.version_control.create_worktree(name=worktree_name)
        if not version_control_result.success or not version_control_result.data:
            logger.warning(
                "Failed to create worktree for EoH generation {} ({}): {}",
                generation_id,
                worktree_name,
                version_control_result.message,
            )
            return None
        worktree = version_control_result.data.get("worktree")
        logger.info(
            "Created worktree for EoH generation {} ({}): {}",
            generation_id,
            worktree_name,
            worktree.path,
        )
        return worktree

    async def plan(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Plan the next EoH algorithm with an explicit operator.

        Args:
            population: Current population (used as default parents).
            generation: Current generation number.
            **kwargs: Must include ``operator``; may include ``parents`` and
                ``background``.

        Returns:
            The planned algorithm insight.

        Raises:
            ValueError: If the operator is not supported.
        """
        operator = kwargs.get("operator", "i1")
        if operator not in self.OPERATOR_TO_SAMPLER:
            raise ValueError(f"Unsupported EoH operator: {operator}")

        sampler = self.sampler_map[self.OPERATOR_TO_SAMPLER[operator]]
        parents = kwargs.get("parents", population)

        start_time = time.time()
        algorithm = await sampler.sample(
            population=parents,
            generation=generation,
            parents=parents,
            background=kwargs.get("background", ""),
        )
        plan_time_ms = (time.time() - start_time) * 1000
        self.state_tracker.record_timing("planner.plan", plan_time_ms)
        if algorithm.timing.llm_planning_ms > 0:
            self.state_tracker.record_timing("provider.chat.planner", algorithm.timing.llm_planning_ms)
        return algorithm
