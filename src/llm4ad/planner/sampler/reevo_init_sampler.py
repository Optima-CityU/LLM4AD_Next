"""ReEvo init sampler: initial population generation.

Migrated from legacy LLM4AD ReEvo method (llm4ad/method/reevo/prompt.py).
ReEvo's init prompt asks the LLM to improve a baseline version. Reuses the
shared EoH sampler base for provider handling and Algorithm construction.
"""

from __future__ import annotations

from typing import Any

from llm4ad.planner.base import Algorithm, InsightType
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.eoh_i1_sampler import _BaseEoHSampler


@BaseSampler.register("reevo_init_sampler")
class ReEvoInitSampler(_BaseEoHSampler):
    """ReEvo init sampler for initial population generation.

    Generates initial algorithms by asking the LLM to improve a baseline
    version (the classic ReEvo v1 -> v2 pattern).
    """

    @property
    def n_parents(self) -> int:
        """Number of parent algorithms required (0 for init)."""
        return 0

    @property
    def name(self) -> str:
        """Get the sampler name."""
        return "reevo_init_sampler"

    async def sample(
        self,
        parents: list[Algorithm],
        generation: int,
        **kwargs: Any,
    ) -> Algorithm:
        """Generate an initial algorithm with an improvement prompt.

        Args:
            parents: Empty list (init has no parents).
            generation: Current generation number.
            **kwargs: Additional parameters (background, template_function).

        Returns:
            Algorithm with initial insight.
        """
        background = kwargs.get("background", "")
        template_text = self._template_text(kwargs)

        prompt = (
            "You are an expert in the domain of optimization heuristics. Your "
            "task is to design heuristics that can effectively solve "
            "optimization problems.\n"
            f"{background}\n"
            f"{template_text}\n"
            "Improve this function to give a better version.\n"
            "Return a concise algorithm name and a one-sentence description of "
            "your improved approach."
        )

        return await self._generate(
            prompt,
            generation=generation,
            insight_type=InsightType.INITIAL,
            operator="reevo_init",
            parent_ids=[],
        )
