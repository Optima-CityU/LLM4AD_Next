"""E2 sampler for MEoH."""

from __future__ import annotations

from llm4ad.infra.provider.base import BaseProvider
from llm4ad.infra.repo_analyzer.base import AnalyzedRepository
from llm4ad.planner.base import Algorithm, InsightType
from llm4ad.planner.memory import Memory
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.meoh_prompt_templates import build_e2_unified_prompt
from llm4ad.planner.sampler.unified_sampler_base import BaseUnifiedSampler


@BaseSampler.register("meoh_e2_sampler")
class MEoHE2Sampler(BaseUnifiedSampler):
    """Generate a backbone-inspired algorithm from multiple parents."""

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict,
        analyzed_repository: AnalyzedRepository,
    ) -> None:
        """Initialize the E2 sampler."""
        super().__init__(provider, memory, config, analyzed_repository)
        self.parent_count = max(2, config.get("selection_num", 2))

    @property
    def n_parents(self) -> int:
        """Return the number of required parents."""
        return self.parent_count

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "meoh_e2_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs) -> Algorithm:
        """Sample a new E2 algorithm."""
        parents = kwargs.get("parents", population)
        if len(parents) < 2:
            raise ValueError("MEoH E2 requires at least two parents")
        prompt = build_e2_unified_prompt(kwargs.get("background", ""), self._first_block(), parents)
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.CROSSOVER,
            operator="e2",
            parent_ids=[parent.id for parent in parents],
        )
