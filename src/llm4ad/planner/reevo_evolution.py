"""ReEvo-specific planner implementation.

Dispatches the ReEvo operators (init / crossover / mutation) to their
samplers and provides a helper for the long-term reflection LLM call that
the orchestrator invokes between generations.
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
from llm4ad.planner.sampler.reevo_samplers import (  # noqa: F401
    ReEvoCrossoverSampler,
    ReEvoInitSampler,
    ReEvoMutationSampler,
    build_long_term_reflection_prompt,
)


@BasePlanner.register("reevo_evolution")
class ReEvoEvolutionPlanner(LLMEvolutionPlanner):
    """Planner with explicit ReEvo operator dispatch and reflection support."""

    OPERATOR_TO_SAMPLER = {
        "init": "reevo_init_sampler",
        "crossover": "reevo_crossover_sampler",
        "mutation": "reevo_mutation_sampler",
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
        """Initialize the ReEvo planner and its samplers."""
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
        """Create a ReEvo worktree."""
        worktree_name = f"reevo_gen_{generation_id}_ind_{algorithm_id}"
        logger.info(f"Creating worktree for ReEvo generation {generation_id} ({worktree_name}) ...")
        version_control_result = self.version_control.create_worktree(name=worktree_name)
        if not version_control_result.success or not version_control_result.data:
            logger.warning(
                "Failed to create worktree for ReEvo generation {} ({}): {}",
                generation_id,
                worktree_name,
                version_control_result.message,
            )
            return None
        worktree = version_control_result.data.get("worktree")
        logger.info(
            "Created worktree for ReEvo generation {} ({}): {}",
            generation_id,
            worktree_name,
            worktree.path,
        )
        return worktree

    async def plan(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Plan the next ReEvo algorithm with an explicit operator."""
        operator = kwargs.get("operator", "init")
        if operator not in self.OPERATOR_TO_SAMPLER:
            raise ValueError(f"Unsupported ReEvo operator: {operator}")

        sampler = self.sampler_map[self.OPERATOR_TO_SAMPLER[operator]]
        parents = kwargs.get("parents", population)

        start_time = time.time()
        algorithm = await sampler.sample(
            population=parents,
            generation=generation,
            parents=parents,
            background=kwargs.get("background", ""),
            long_term_reflection=kwargs.get("long_term_reflection", ""),
        )
        plan_time_ms = (time.time() - start_time) * 1000
        self.state_tracker.record_timing("planner.plan", plan_time_ms)
        if algorithm.timing.llm_planning_ms > 0:
            self.state_tracker.record_timing("provider.chat.planner", algorithm.timing.llm_planning_ms)
        return algorithm

    async def reflect_long_term(
        self,
        background: str,
        prior_long_term: str,
        new_short_terms: list[str],
    ) -> str:
        """Distil accumulated short-term hints into a long-term reflection.

        Args:
            background: Task background description.
            prior_long_term: The previous long-term reflection.
            new_short_terms: Recently gathered short-term hints.

        Returns:
            The new long-term reflection text (empty string on failure).
        """
        prompt = build_long_term_reflection_prompt(background, prior_long_term, new_short_terms)
        try:
            response = await self.provider.generate(prompt, request_stage="planner")
            return (response.text or "").strip()
        except Exception as exc:
            logger.warning(f"[ReEvo] Long-term reflection failed: {exc}")
            return prior_long_term
