"""ReEvo-specific planner implementation with reflection state management.

Migrated from legacy LLM4AD ReEvo method (llm4ad/method/reevo/reevo.py).

The legacy ReEvo main loop maintained reflection state across generations:
- Each crossover produces a *short-term reflection* (why the better parent wins).
- Every ``pop_size`` crossovers, the accumulated short-term reflections are
  distilled into a *long-term reflection*.
- Elite mutations are then guided by that long-term reflection.

New-platform samplers are stateless, so this planner carries the reflection
state. It extends ``LLMEvolutionPlanner`` (reusing its sampler wiring and
``implement``/``build`` behaviour) and overrides ``plan`` to:
1. Route generation-0 / empty-population requests to the init sampler.
2. Alternate between crossover (with short-term reflection capture) and
   long-term-reflection-guided mutation on a ``pop_size`` cadence.

The orchestrator does not need to know about reflections — it simply calls
``plan(population, generation)`` as usual. This planner works with the existing
``island_ga`` orchestrator.
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from llm4ad.planner.base import Algorithm, BasePlanner
from llm4ad.planner.llm_evolution import LLMEvolutionPlanner


@BasePlanner.register("reevo_evolution")
class ReEvoEvolutionPlanner(LLMEvolutionPlanner):
    """Planner that manages ReEvo short-term and long-term reflection state.

    Expects three samplers to be configured (by registered name):
    ``reevo_init_sampler``, ``reevo_crossover_sampler``,
    ``reevo_mutation_sampler``. Any missing sampler degrades gracefully
    (e.g. no mutation sampler -> crossover only).
    """

    #: How many recent short-term reflections feed a long-term reflection.
    MAX_SHORT_TERM: int = 5

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the ReEvo planner and reflection state."""
        super().__init__(*args, **kwargs)
        self._short_term_reflections: list[str] = []
        self._long_term_reflection: str = ""
        self._crossover_count: int = 0
        # ``pop_size`` controls the long-term reflection cadence; read from
        # the planner config with a sensible default.
        self._pop_size: int = int(self._config_value("pop_size", default=10))
        self._mutation_rate: float = float(self._config_value("mutation_rate", default=0.5))

    def _config_value(self, key: str, default: Any) -> Any:
        """Read a value from the planner config (dict or PlannerConfig)."""
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    def _get_sampler(self, name: str):
        """Return a configured sampler by registered name, or None."""
        return self.sampler_map.get(name)

    async def plan(
        self,
        population: list[Algorithm],
        generation: int,
        **kwargs: Any,
    ) -> Algorithm:
        """Plan the next ReEvo individual with reflection-guided operators.

        Args:
            population: Current population of algorithms.
            generation: Current generation number.
            **kwargs: Additional planning parameters (background, etc.).

        Returns:
            New algorithm individual with an insight description.
        """
        start_time = time.time()

        init_sampler = self._get_sampler("reevo_init_sampler")
        crossover_sampler = self._get_sampler("reevo_crossover_sampler")
        mutation_sampler = self._get_sampler("reevo_mutation_sampler")

        # Generation 0 or empty population -> initialization.
        if len(population) == 0 or generation == 0:
            sampler = init_sampler or crossover_sampler or self.samplers[0]
            algorithm = await sampler.sample(
                parents=[], generation=generation, **kwargs
            )
            self._record_plan_timing(start_time, algorithm)
            return algorithm

        # Decide operator: mutation on the long-term-reflection cadence,
        # crossover otherwise.
        use_mutation = (
            mutation_sampler is not None
            and self._long_term_reflection
            and self._crossover_count > 0
            and self._crossover_count % max(1, int(self._pop_size * self._mutation_rate)) == 0
        )

        if use_mutation:
            elite = max(population, key=lambda a: a.score)
            algorithm = await mutation_sampler.sample(
                parents=[elite],
                generation=generation,
                long_term_reflection=self._long_term_reflection,
                **kwargs,
            )
        else:
            sampler = crossover_sampler or self.samplers[0]
            parents = self.select_parents(
                population, sampler.n_parents, self.config_parent_selection_strategy()
            )
            algorithm = await sampler.sample(
                parents=parents, generation=generation, **kwargs
            )
            self._crossover_count += 1
            # Capture the short-term reflection the crossover sampler stored.
            reflection = algorithm.custom_metadata.get("short_term_reflection", "")
            if reflection:
                self._short_term_reflections.append(reflection)

            # Refresh the long-term reflection on the pop_size cadence.
            if self._crossover_count % self._pop_size == 0:
                await self._update_long_term_reflection(kwargs.get("background", ""))

        self._record_plan_timing(start_time, algorithm)
        return algorithm

    def config_parent_selection_strategy(self) -> str:
        """Return the parent selection strategy from config."""
        if isinstance(self.config, dict):
            return self.config.get("parent_selection_strategy", "tournament")
        return getattr(self.config, "parent_selection_strategy", "tournament")

    async def _update_long_term_reflection(self, background: str) -> None:
        """Distill recent short-term reflections into a long-term reflection.

        Args:
            background: Task background for prompt context.
        """
        if not self._short_term_reflections:
            return

        recent = self._short_term_reflections[-self.MAX_SHORT_TERM :]
        joined = "\n".join(f"- {r}" for r in recent)
        prompt = (
            "You are an expert in optimization heuristics. Give constructive "
            "hints for designing better heuristics (under 50 words).\n"
            f"Task: {background}\n"
            f"Prior long-term reflection:\n{self._long_term_reflection or '(none)'}\n"
            f"Newly gained insights:\n{joined}\n"
            "Write concise constructive hints combining prior reflection and "
            "the new insights."
        )
        try:
            response = await self.provider.generate(
                prompt,
                temperature=0.5,
                max_tokens=192,
                request_stage="planner",
            )
            text = (response.text or "").strip()
            if text:
                self._long_term_reflection = text
                logger.info("[ReEvo] Updated long-term reflection.")
        except Exception as exc:  # noqa: BLE001 - reflection is best-effort
            logger.warning(f"[ReEvo] Long-term reflection update failed: {exc}")

    def _record_plan_timing(self, start_time: float, algorithm: Algorithm) -> None:
        """Record plan timing into the state tracker."""
        plan_time_ms = (time.time() - start_time) * 1000
        self.state_tracker.record_timing("planner.plan", plan_time_ms)
        if algorithm.timing.llm_planning_ms > 0:
            self.state_tracker.record_timing(
                "provider.chat.planner", algorithm.timing.llm_planning_ms
            )
