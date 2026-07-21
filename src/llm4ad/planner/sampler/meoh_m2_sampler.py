"""M2 sampler for MEoH."""

from __future__ import annotations

from llm4ad.planner.base import Algorithm, InsightType
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.meoh_prompt_templates import build_m2_unified_prompt
from llm4ad.planner.sampler.unified_sampler_base import BaseUnifiedSampler


@BaseSampler.register("meoh_m2_sampler")
class MEoHM2Sampler(BaseUnifiedSampler):
    """Generate a parameter-focused mutation from one parent."""

    @property
    def n_parents(self) -> int:
        """Return the number of required parents."""
        return 1

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "meoh_m2_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs) -> Algorithm:
        """Sample a new M2 algorithm."""
        parents = kwargs.get("parents", population)
        if not parents:
            raise ValueError("MEoH M2 requires one parent")
        parent = parents[0]
        prompt = build_m2_unified_prompt(kwargs.get("background", ""), self._first_block(), parent)
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.MUTATION,
            operator="m2",
            parent_ids=[parent.id],
        )
