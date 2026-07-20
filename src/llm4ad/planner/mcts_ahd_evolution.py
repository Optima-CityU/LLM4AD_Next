"""MCTS-AHD planner implementation.

Dispatches the MCTS-AHD operators (i1/e1/e2/m1/m2/s1) to their samplers. The
orchestrator drives the tree search and selects the parents/paths for each
operator; this planner only maps operator -> sampler and issues the LLM call.
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
from llm4ad.planner.sampler.mcts_ahd_samplers import (  # noqa: F401
    MCTSE1Sampler,
    MCTSE2Sampler,
    MCTSI1Sampler,
    MCTSM1Sampler,
    MCTSM2Sampler,
    MCTSS1Sampler,
)


@BasePlanner.register("mcts_ahd_evolution")
class MCTSAHDEvolutionPlanner(LLMEvolutionPlanner):
    """Planner with explicit MCTS-AHD operator dispatch."""

    OPERATOR_TO_SAMPLER = {
        "i1": "mcts_i1_sampler",
        "e1": "mcts_e1_sampler",
        "e2": "mcts_e2_sampler",
        "m1": "mcts_m1_sampler",
        "m2": "mcts_m2_sampler",
        "s1": "mcts_s1_sampler",
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
        """Initialize the MCTS-AHD planner and its samplers."""
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
        """Create an MCTS-AHD worktree."""
        worktree_name = f"mcts_gen_{generation_id}_ind_{algorithm_id}"
        logger.info(f"Creating worktree for MCTS-AHD iteration {generation_id} ({worktree_name}) ...")
        version_control_result = self.version_control.create_worktree(name=worktree_name)
        if not version_control_result.success or not version_control_result.data:
            logger.warning(
                "Failed to create worktree for MCTS-AHD iteration {} ({}): {}",
                generation_id,
                worktree_name,
                version_control_result.message,
            )
            return None
        worktree = version_control_result.data.get("worktree")
        logger.info(
            "Created worktree for MCTS-AHD iteration {} ({}): {}",
            generation_id,
            worktree_name,
            worktree.path,
        )
        return worktree

    async def plan(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Plan the next MCTS-AHD algorithm with an explicit operator."""
        operator = kwargs.get("operator", "i1")
        if operator not in self.OPERATOR_TO_SAMPLER:
            raise ValueError(f"Unsupported MCTS-AHD operator: {operator}")

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
