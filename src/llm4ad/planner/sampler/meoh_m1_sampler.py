"""M1 sampler for MEoH."""

from __future__ import annotations

from llm4ad.planner.base import Algorithm, InsightType
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.meoh_prompt_templates import build_m1_unified_prompt
from llm4ad.planner.sampler.unified_sampler_base import BaseUnifiedSampler


@BaseSampler.register("meoh_m1_sampler")
class MEoHM1Sampler(BaseUnifiedSampler):
    """Generate a structural mutation from one parent."""

    @property
    def n_parents(self) -> int:
        """Return the number of required parents."""
        return 1

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "meoh_m1_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs) -> Algorithm:
        """Sample a new M1 algorithm."""
        parents = kwargs.get("parents", population)
        if not parents:
            raise ValueError("MEoH M1 requires one parent")
        parent = parents[0]
        prompt = build_m1_unified_prompt(kwargs.get("background", ""), self._first_block(), parent)
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.MUTATION,
            operator="m1",
            parent_ids=[parent.id],
        )
