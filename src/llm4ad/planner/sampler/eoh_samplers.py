"""EoH (Evolution of Heuristics) samplers.

Standalone I1/E1/E2/M1/M2 operator samplers for EoH. Each subclasses the
neutral ``BaseUnifiedSampler`` (thought + code in one LLM call) and supplies
its own EoH prompt. EoH does not share sampler classes with any other method.

Reference:
    Fei Liu et al. "Evolution of Heuristics: Towards Efficient Automatic
    Algorithm Design Using Large Language Model." ICML 2024.
"""

from __future__ import annotations

from typing import Any

from llm4ad.infra.provider.base import BaseProvider
from llm4ad.infra.repo_analyzer.base import AnalyzedRepository
from llm4ad.planner.base import Algorithm, InsightType
from llm4ad.planner.memory import Memory
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.eoh_prompt_templates import (
    build_e1_unified_prompt,
    build_e2_unified_prompt,
    build_i1_unified_prompt,
    build_m1_unified_prompt,
    build_m2_unified_prompt,
)
from llm4ad.planner.sampler.unified_sampler_base import BaseUnifiedSampler


@BaseSampler.register("eoh_init_sampler")
class EoHInitSampler(BaseUnifiedSampler):
    """Generate an initial EoH algorithm (I1)."""

    @property
    def n_parents(self) -> int:
        """No parents required for initialization."""
        return 0

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "eoh_init_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Sample a new I1 algorithm."""
        prompt = build_i1_unified_prompt(kwargs.get("background", ""), self._first_block())
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.INITIAL,
            operator="i1",
            parent_ids=[],
        )


@BaseSampler.register("eoh_e1_sampler")
class EoHE1Sampler(BaseUnifiedSampler):
    """Generate a distinct new algorithm from multiple parents (E1)."""

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository,
    ) -> None:
        """Initialize the E1 sampler and resolve the parent count."""
        super().__init__(provider, memory, config, analyzed_repository)
        self.parent_count = max(2, self._get_config_value("selection_num", config, default=2))

    @property
    def n_parents(self) -> int:
        """Return the number of required parents."""
        return self.parent_count

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "eoh_e1_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Sample a new E1 algorithm."""
        parents = kwargs.get("parents", population)
        if len(parents) < 2:
            raise ValueError("EoH E1 requires at least two parents")
        prompt = build_e1_unified_prompt(kwargs.get("background", ""), self._first_block(), parents)
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.CROSSOVER,
            operator="e1",
            parent_ids=[parent.id for parent in parents],
        )


@BaseSampler.register("eoh_e2_sampler")
class EoHE2Sampler(BaseUnifiedSampler):
    """Generate a backbone-inspired algorithm from multiple parents (E2)."""

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository,
    ) -> None:
        """Initialize the E2 sampler and resolve the parent count."""
        super().__init__(provider, memory, config, analyzed_repository)
        self.parent_count = max(2, self._get_config_value("selection_num", config, default=2))

    @property
    def n_parents(self) -> int:
        """Return the number of required parents."""
        return self.parent_count

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "eoh_e2_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Sample a new E2 algorithm."""
        parents = kwargs.get("parents", population)
        if len(parents) < 2:
            raise ValueError("EoH E2 requires at least two parents")
        prompt = build_e2_unified_prompt(kwargs.get("background", ""), self._first_block(), parents)
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.CROSSOVER,
            operator="e2",
            parent_ids=[parent.id for parent in parents],
        )


@BaseSampler.register("eoh_m1_sampler")
class EoHM1Sampler(BaseUnifiedSampler):
    """Generate a structural mutation from one parent (M1)."""

    @property
    def n_parents(self) -> int:
        """Return the number of required parents."""
        return 1

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "eoh_m1_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Sample a new M1 algorithm."""
        parents = kwargs.get("parents", population)
        if not parents:
            raise ValueError("EoH M1 requires one parent")
        parent = parents[0]
        prompt = build_m1_unified_prompt(kwargs.get("background", ""), self._first_block(), parent)
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.MUTATION,
            operator="m1",
            parent_ids=[parent.id],
        )


@BaseSampler.register("eoh_m2_sampler")
class EoHM2Sampler(BaseUnifiedSampler):
    """Generate a parameter-focused mutation from one parent (M2)."""

    @property
    def n_parents(self) -> int:
        """Return the number of required parents."""
        return 1

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "eoh_m2_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Sample a new M2 algorithm."""
        parents = kwargs.get("parents", population)
        if not parents:
            raise ValueError("EoH M2 requires one parent")
        parent = parents[0]
        prompt = build_m2_unified_prompt(kwargs.get("background", ""), self._first_block(), parent)
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.MUTATION,
            operator="m2",
            parent_ids=[parent.id],
        )
