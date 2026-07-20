"""Initial sampler for MEoH."""

from __future__ import annotations

from llm4ad.planner.base import Algorithm, InsightType
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.meoh_prompt_templates import build_i1_unified_prompt
from llm4ad.planner.sampler.unified_sampler_base import BaseUnifiedSampler


@BaseSampler.register("meoh_init_sampler")
class MEoHInitSampler(BaseUnifiedSampler):
    """Generate an initial MEoH insight."""

    @property
    def n_parents(self) -> int:
        """Return the number of required parents."""
        return 0

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "meoh_init_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs) -> Algorithm:
        """Sample a new I1 algorithm."""
        prompt = build_i1_unified_prompt(kwargs.get("background", ""), self._first_block())
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.INITIAL,
            operator="i1",
            parent_ids=[],
        )
