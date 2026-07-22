"""DyCA-specific samplers for cluster-aware algorithm evolution.

These samplers implement the evolutionary operators from the DyCA
(Dynamic Clustering Algorithm) framework. Each sampler generates
algorithm insights (natural language descriptions) guided by
cluster context and specific evolutionary strategies:

- E1Sampler: Incremental evolution from elite archive
- E2Sampler: Structural evolution for breakthroughs
- M1Sampler: Fine-tuning mutation
- M2Sampler: Creative/random mutation
- SummarySampler: Multi-algorithm synthesis
- ComplementaryCrossSampler: Cross-cluster crossover
"""

import time
from typing import Any

from pydantic import BaseModel, Field

from llm4ad.infra.provider.base import BaseProvider
from llm4ad.infra.repo_analyzer.base import AnalyzedRepository
from llm4ad.orchestrator.clustering_utils import cluster_score
from llm4ad.planner.base import Algorithm, GenerationMetadata, InsightType
from llm4ad.planner.memory import Memory
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.dyca_prompt_templates import (
    COMPLEMENTARY_CROSS_PROMPT,
    E1_EVOLVE_PROMPT,
    E2_EVOLVE_PROMPT,
    M1_MUTATE_PROMPT,
    M2_MUTATE_PROMPT,
    SUMMARY_PROMPT,
)


class _AlgorithmSchema(BaseModel):
    """Schema for LLM-generated algorithm insight."""

    name: str = Field(..., description="Concise name for the algorithm")
    description: str = Field(..., description="Detailed description of the algorithm")


async def _call_llm_and_build_algorithm(
    provider: BaseProvider,
    prompt: str,
    temperature: float,
    max_tokens: int,
    insight_type: InsightType,
    operator_name: str,
    parent_ids: list[str],
    generation: int,
    operation_params: dict[str, Any] | None = None,
) -> Algorithm:
    """Call LLM and build an Algorithm from the response.

    Args:
        provider: LLM provider.
        prompt: Formatted prompt string.
        temperature: Sampling temperature.
        operation_params: Optional operator-specific metadata.
        max_tokens: Maximum tokens.
        insight_type: Insight type for the algorithm.
        operator_name: Name of the sampler operator.
        parent_ids: IDs of parent algorithms.
        generation: Current generation number.

    Returns:
        New Algorithm with description and metadata.

    Raises:
        ValueError: If LLM response cannot be parsed.
    """
    start_time = time.time()

    response = await provider.generate(
        prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        schema=_AlgorithmSchema,
    )

    generation_time_ms = (time.time() - start_time) * 1000

    if not response.parsed:
        raise ValueError(
            f"LLM response could not be parsed for {operator_name} generation."
        )

    schema: _AlgorithmSchema = response.parsed  # type: ignore

    algorithm = Algorithm(
        insight_type=insight_type,
        description=schema.description,
        name=schema.name,
        parent_ids=parent_ids,
        generation=generation,
    )
    algorithm.custom_metadata["dyca_operator"] = operator_name

    algorithm.generation_meta = GenerationMetadata(
        operator=operator_name,
        llm_provider=provider.__class__.__name__,
        llm_model=getattr(provider, "model", "unknown"),
        temperature=temperature,
        generation_time_ms=generation_time_ms,
        tokens_used=response.prompt_tokens + response.completion_tokens,
        agent_name=operator_name,
        operation_params=operation_params or {},
        change_description=algorithm.description,
    )

    return algorithm


@BaseSampler.register("e1_sampler")
class E1Sampler(BaseSampler):
    """Incremental evolution sampler from elite archive.

    Refines a high-performing parent algorithm with cluster-aware
    incremental improvements.
    """

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository | None = None,
    ):
        """Initialize E1Sampler.

        Args:
            provider: LLM provider.
            memory: Memory system.
            config: Sampler configuration.
            analyzed_repository: Pre-analyzed repository.
        """
        super().__init__(provider, memory, config, analyzed_repository)
        provider_cfg = getattr(provider, 'config', None) or {}
        self.temperature = self._get_config_value("temperature", config, provider_cfg, default=0.7)
        self.max_tokens = self._get_config_value("max_tokens", config, provider_cfg, default=4096)

    @property
    def n_parents(self) -> int:
        """E1 requires 1 parent."""
        return 1

    @property
    def name(self) -> str:
        """Get sampler name."""
        return "e1_sampler"

    async def sample(
        self,
        population: list[Algorithm],
        generation: int,
        **kwargs: Any,
    ) -> Algorithm:
        """Sample an incrementally improved algorithm.

        Args:
            population: Current population.
            generation: Current generation number.
            **kwargs: Must contain 'parent', 'cluster_instances',
                'background'. Optional: 'top_k_algorithms'.

        Returns:
            New Algorithm with improvement insight.
        """
        parent: Algorithm = kwargs["parent"]
        cluster_instances: list[str] = kwargs.get("cluster_instances", [])
        background: str = kwargs.get("background", "")
        cluster_id: int = kwargs.get("cluster_id", 0)
        top_k: list[Algorithm] = kwargs.get("top_k_algorithms", [])

        # Build memory context
        memory_context = await self.memory.aget_prompt_context(
            query=background,
            context={
                "sampler": "dyca_e1",
                "cluster_id": cluster_id,
                "parent_score": parent.score,
                "parent_description": parent.description,
            },
        ) if self.memory else ""

        parent_cluster = cluster_score(parent, cluster_instances)
        all_cluster_scores = [
            cluster_score(a, cluster_instances)
            for a in population if a.is_evaluated()
        ]
        best_cs = max(all_cluster_scores) if all_cluster_scores else 0.0
        worst_cs = min(all_cluster_scores) if all_cluster_scores else 0.0

        top_desc = "\n".join(
            f"- Score: {cluster_score(a, cluster_instances):.4f} | {a.description[:200]}"
            for a in top_k[:3]
        )

        prompt = E1_EVOLVE_PROMPT.format(
            background=background,
            memory_context=memory_context,
            cluster_id=cluster_id,
            n_instances=len(cluster_instances),
            best_cluster_score=best_cs,
            worst_cluster_score=worst_cs,
            parent_cluster_score=parent_cluster,
            parent_score=parent.score,
            parent_description=parent.description,
            top_algorithms=top_desc or "No other algorithms available.",
        )

        return await _call_llm_and_build_algorithm(
            provider=self.provider,
            prompt=prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            insight_type=InsightType.MUTATION,
            operator_name="e1_sampler",
            parent_ids=[parent.id],
            generation=generation,
            operation_params={
                "cluster_id": cluster_id,
                "parent_id": parent.id,
                "parent_score": parent.score,
                "parent_cluster_score": parent_cluster,
                "parent_description": parent.description,
                "best_cluster_score": best_cs,
                "worst_cluster_score": worst_cs,
            },
        )


@BaseSampler.register("e2_sampler")
class E2Sampler(BaseSampler):
    """Structural evolution sampler for breakthroughs.

    Generates substantially different algorithm approaches using
    cluster context for targeted innovation.
    """

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository | None = None,
    ):
        """Initialize E2Sampler.

        Args:
            provider: LLM provider.
            memory: Memory system.
            config: Sampler configuration.
            analyzed_repository: Pre-analyzed repository.
        """
        super().__init__(provider, memory, config, analyzed_repository)
        provider_cfg = getattr(provider, 'config', None) or {}
        self.temperature = self._get_config_value("temperature", config, provider_cfg, default=0.9)
        self.max_tokens = self._get_config_value("max_tokens", config, provider_cfg, default=4096)

    @property
    def n_parents(self) -> int:
        """E2 requires 1 parent."""
        return 1

    @property
    def name(self) -> str:
        """Get sampler name."""
        return "e2_sampler"

    async def sample(
        self,
        population: list[Algorithm],
        generation: int,
        **kwargs: Any,
    ) -> Algorithm:
        """Sample a structurally different algorithm.

        Args:
            population: Current population.
            generation: Current generation number.
            **kwargs: Must contain 'parent', 'cluster_instances', 'background'.

        Returns:
            New Algorithm with breakthrough insight.
        """
        parent: Algorithm = kwargs["parent"]
        cluster_instances: list[str] = kwargs.get("cluster_instances", [])
        background: str = kwargs.get("background", "")
        cluster_id: int = kwargs.get("cluster_id", 0)

        # Build memory context
        memory_context = await self.memory.aget_prompt_context(
            query=background,
            context={
                "sampler": "dyca_e2",
                "cluster_id": cluster_id,
                "parent_score": parent.score,
                "parent_description": parent.description,
            },
        ) if self.memory else ""

        parent_cluster = cluster_score(parent, cluster_instances)
        all_cluster_scores = [
            cluster_score(a, cluster_instances)
            for a in population if a.is_evaluated()
        ]
        best_cs = max(all_cluster_scores) if all_cluster_scores else 0.0

        prompt = E2_EVOLVE_PROMPT.format(
            background=background,
            memory_context=memory_context,
            cluster_id=cluster_id,
            n_instances=len(cluster_instances),
            best_cluster_score=best_cs,
            parent_cluster_score=parent_cluster,
            parent_score=parent.score,
            parent_description=parent.description,
        )

        return await _call_llm_and_build_algorithm(
            provider=self.provider,
            prompt=prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            insight_type=InsightType.MUTATION,
            operator_name="e2_sampler",
            parent_ids=[parent.id],
            generation=generation,
            operation_params={
                "cluster_id": cluster_id,
                "parent_id": parent.id,
                "parent_score": parent.score,
                "parent_cluster_score": parent_cluster,
                "parent_description": parent.description,
                "best_cluster_score": best_cs,
            },
        )


@BaseSampler.register("m1_sampler")
class M1Sampler(BaseSampler):
    """Fine-tuning mutation sampler.

    Makes small, targeted adjustments to parameters and logic
    while keeping the core algorithm structure intact.
    """

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository | None = None,
    ):
        """Initialize M1Sampler.

        Args:
            provider: LLM provider.
            memory: Memory system.
            config: Sampler configuration.
            analyzed_repository: Pre-analyzed repository.
        """
        super().__init__(provider, memory, config, analyzed_repository)
        provider_cfg = getattr(provider, 'config', None) or {}
        self.temperature = self._get_config_value("temperature", config, provider_cfg, default=0.5)
        self.max_tokens = self._get_config_value("max_tokens", config, provider_cfg, default=1024)

    @property
    def n_parents(self) -> int:
        """M1 requires 1 parent."""
        return 1

    @property
    def name(self) -> str:
        """Get sampler name."""
        return "m1_sampler"

    async def sample(
        self,
        population: list[Algorithm],
        generation: int,
        **kwargs: Any,
    ) -> Algorithm:
        """Sample a fine-tuned mutation.

        Args:
            population: Current population.
            generation: Current generation number.
            **kwargs: Must contain 'parent'. Optional: 'cluster_instances', 'background'.

        Returns:
            New Algorithm with fine-tuning insight.
        """
        parent: Algorithm = kwargs["parent"]
        cluster_instances: list[str] = kwargs.get("cluster_instances", [])
        background: str = kwargs.get("background", "")
        cluster_id: int = kwargs.get("cluster_id", 0)

        # Build memory context
        memory_context = await self.memory.aget_prompt_context(
            query=background,
            context={
                "sampler": "dyca_m1",
                "cluster_id": cluster_id,
                "parent_score": parent.score,
                "parent_description": parent.description,
            },
        ) if self.memory else ""

        parent_cluster = cluster_score(parent, cluster_instances)
        all_cluster_scores = [
            cluster_score(a, cluster_instances)
            for a in population if a.is_evaluated()
        ]
        best_cs = max(all_cluster_scores) if all_cluster_scores else 0.0

        prompt = M1_MUTATE_PROMPT.format(
            background=background,
            memory_context=memory_context,
            cluster_id=cluster_id,
            best_cluster_score=best_cs,
            parent_cluster_score=parent_cluster,
            parent_description=parent.description,
        )

        return await _call_llm_and_build_algorithm(
            provider=self.provider,
            prompt=prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            insight_type=InsightType.MUTATION,
            operator_name="m1_sampler",
            parent_ids=[parent.id],
            generation=generation,
            operation_params={
                "cluster_id": cluster_id,
                "parent_id": parent.id,
                "parent_score": parent.score,
                "parent_cluster_score": parent_cluster,
                "parent_description": parent.description,
                "best_cluster_score": best_cs,
            },
        )


@BaseSampler.register("m2_sampler")
class M2Sampler(BaseSampler):
    """Creative/random mutation sampler.

    Introduces unexpected variations by encouraging the LLM to
    explore unconventional approaches and cross-domain ideas.
    """

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository | None = None,
    ):
        """Initialize M2Sampler.

        Args:
            provider: LLM provider.
            memory: Memory system.
            config: Sampler configuration.
            analyzed_repository: Pre-analyzed repository.
        """
        super().__init__(provider, memory, config, analyzed_repository)
        provider_cfg = getattr(provider, 'config', None) or {}
        self.temperature = self._get_config_value("temperature", config, provider_cfg, default=1.0)
        self.max_tokens = self._get_config_value("max_tokens", config, provider_cfg, default=4096)

    @property
    def n_parents(self) -> int:
        """M2 requires 1 parent."""
        return 1

    @property
    def name(self) -> str:
        """Get sampler name."""
        return "m2_sampler"

    async def sample(
        self,
        population: list[Algorithm],
        generation: int,
        **kwargs: Any,
    ) -> Algorithm:
        """Sample a creative mutation.

        Args:
            population: Current population.
            generation: Current generation number.
            **kwargs: Must contain 'parent'. Optional: 'background'.

        Returns:
            New Algorithm with creative insight.
        """
        parent: Algorithm = kwargs["parent"]
        background: str = kwargs.get("background", "")

        # Build memory context
        memory_context = await self.memory.aget_prompt_context(
            query=background,
            context={
                "sampler": "dyca_m2",
                "parent_score": parent.score,
                "parent_description": parent.description,
            },
        ) if self.memory else ""

        prompt = M2_MUTATE_PROMPT.format(
            background=background,
            memory_context=memory_context,
            parent_score=parent.score,
            parent_description=parent.description,
        )

        return await _call_llm_and_build_algorithm(
            provider=self.provider,
            prompt=prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            insight_type=InsightType.MUTATION,
            operator_name="m2_sampler",
            parent_ids=[parent.id],
            generation=generation,
            operation_params={
                "parent_id": parent.id,
                "parent_score": parent.score,
                "parent_description": parent.description,
            },
        )


@BaseSampler.register("summary_sampler")
class SummarySampler(BaseSampler):
    """Multi-algorithm synthesis sampler.

    Analyzes patterns across multiple algorithms and generates
    a synthesized design that combines their best ideas.
    """

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository | None = None,
    ):
        """Initialize SummarySampler.

        Args:
            provider: LLM provider.
            memory: Memory system.
            config: Sampler configuration.
            analyzed_repository: Pre-analyzed repository.
        """
        super().__init__(provider, memory, config, analyzed_repository)
        provider_cfg = getattr(provider, 'config', None) or {}
        self.temperature = self._get_config_value("temperature", config, provider_cfg, default=0.7)
        self.max_tokens = self._get_config_value("max_tokens", config, provider_cfg, default=3072)

    @property
    def n_parents(self) -> int:
        """Summary requires at least 2 parents."""
        return 2

    @property
    def name(self) -> str:
        """Get sampler name."""
        return "summary_sampler"

    async def sample(
        self,
        population: list[Algorithm],
        generation: int,
        **kwargs: Any,
    ) -> Algorithm:
        """Sample a synthesized algorithm from multiple parents.

        Args:
            population: Current population.
            generation: Current generation number.
            **kwargs: Must contain 'parents' (list of 2+ algorithms).
                Optional: 'background'.

        Returns:
            New Algorithm with synthesized insight.
        """
        parents: list[Algorithm] = kwargs["parents"]
        background: str = kwargs.get("background", "")

        # Build memory context
        memory_context = await self.memory.aget_prompt_context(
            query=background,
            context={
                "sampler": "dyca_summary",
                "parents": [
                    {"score": parent.score, "description": parent.description}
                    for parent in parents
                ],
            },
        ) if self.memory else ""

        algo_descs = "\n\n".join(
            f"## Algorithm {i + 1}: {a.name}\n"
            f"Score: {a.score:.4f}\n"
            f"Description: {a.description}"
            for i, a in enumerate(parents)
        )

        prompt = SUMMARY_PROMPT.format(
            background=background,
            memory_context=memory_context,
            algorithm_descriptions=algo_descs,
        )

        return await _call_llm_and_build_algorithm(
            provider=self.provider,
            prompt=prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            insight_type=InsightType.CROSSOVER,
            operator_name="summary_sampler",
            parent_ids=[p.id for p in parents],
            generation=generation,
            operation_params={
                "parents": [
                    {
                        "id": parent.id,
                        "score": parent.score,
                        "description": parent.description,
                    }
                    for parent in parents
                ],
            },
        )


@BaseSampler.register("complementary_cross_sampler")
class ComplementaryCrossSampler(BaseSampler):
    """Cross-cluster crossover sampler.

    Combines algorithms from different clusters to create designs
    that perform well across diverse instance types.
    """

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository | None = None,
    ):
        """Initialize ComplementaryCrossSampler.

        Args:
            provider: LLM provider.
            memory: Memory system.
            config: Sampler configuration.
            analyzed_repository: Pre-analyzed repository.
        """
        super().__init__(provider, memory, config, analyzed_repository)
        provider_cfg = getattr(provider, 'config', None) or {}
        self.temperature = self._get_config_value("temperature", config, provider_cfg, default=0.8)
        self.max_tokens = self._get_config_value("max_tokens", config, provider_cfg, default=4096)

    @property
    def n_parents(self) -> int:
        """Complementary crossover requires 2 parents."""
        return 2

    @property
    def name(self) -> str:
        """Get sampler name."""
        return "complementary_cross_sampler"

    async def sample(
        self,
        population: list[Algorithm],
        generation: int,
        **kwargs: Any,
    ) -> Algorithm:
        """Sample a cross-cluster combined algorithm.

        Args:
            population: Current population.
            generation: Current generation number.
            **kwargs: Must contain 'parents' (2 algorithms from different clusters).
                Optional: 'background', 'cluster_id_1', 'cluster_id_2'.

        Returns:
            New Algorithm with combined insight.
        """
        parents: list[Algorithm] = kwargs["parents"]
        background: str = kwargs.get("background", "")
        cluster_id_1: int = kwargs.get("cluster_id_1", 0)
        cluster_id_2: int = kwargs.get("cluster_id_2", 1)
        cluster_instances_1: list[str] = kwargs.get("cluster_instances_1", [])
        cluster_instances_2: list[str] = kwargs.get("cluster_instances_2", [])

        # Build memory context
        memory_context = await self.memory.aget_prompt_context(
            query=background,
            context={
                "sampler": "dyca_complementary_cross",
                "cluster_id_1": cluster_id_1,
                "cluster_id_2": cluster_id_2,
                "parent_1_score": parents[0].score,
                "parent_1_description": parents[0].description,
                "parent_2_score": parents[1].score,
                "parent_2_description": parents[1].description,
            },
        ) if self.memory else ""

        score1 = cluster_score(parents[0], cluster_instances_1) if cluster_instances_1 else parents[0].score
        score2 = cluster_score(parents[1], cluster_instances_2) if cluster_instances_2 else parents[1].score

        prompt = COMPLEMENTARY_CROSS_PROMPT.format(
            background=background,
            memory_context=memory_context,
            cluster_id_1=cluster_id_1,
            score1=score1,
            description1=parents[0].description,
            cluster_id_2=cluster_id_2,
            score2=score2,
            description2=parents[1].description,
        )

        return await _call_llm_and_build_algorithm(
            provider=self.provider,
            prompt=prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            insight_type=InsightType.CROSSOVER,
            operator_name="complementary_cross_sampler",
            parent_ids=[p.id for p in parents],
            generation=generation,
            operation_params={
                "cluster_id_1": cluster_id_1,
                "cluster_id_2": cluster_id_2,
                "parent_1_id": parents[0].id,
                "parent_1_score": score1,
                "parent_1_description": parents[0].description,
                "parent_2_id": parents[1].id,
                "parent_2_score": score2,
                "parent_2_description": parents[1].description,
            },
        )
