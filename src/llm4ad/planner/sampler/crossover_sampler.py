"""Crossover sampler that combines two parent algorithms into a child.

This sampler takes two parent algorithms and generates a new child algorithm
that combines the best ideas from both parents. Uses the CROSSOVER_ALGORITHM
template to guide the LLM in generating meaningful combinations.
"""

import time
from typing import Any

from pydantic import BaseModel, Field

from llm4ad.infra.provider.base import BaseProvider
from llm4ad.infra.repo_analyzer.base import AnalyzedRepository
from llm4ad.planner.base import Algorithm, GenerationMetadata, InsightType
from llm4ad.planner.memory import Memory
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.prompt_templates import (
    CROSSOVER_ALGORITHM,
    format_parent_algorithm_context,
)


@BaseSampler.register("crossover_sampler")
class CrossoverSampler(BaseSampler):
    """Crossover sampler that combines two parent algorithms into a child.

    Takes two parent algorithms and generates a new algorithm that combines
    the best ideas from both parents. The crossover is guided by the
    CROSSOVER_ALGORITHM prompt template.
    """

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository | None = None,
    ):
        """Initialize the CrossoverSampler.

        Args:
            provider: LLM provider for generating crossovers
            memory: Memory system for storing/retrieving past experiences
            config: Sampler configuration:
                - prompt_template: Custom prompt template to use (optional)
                - temperature: Sampling temperature (default: 0.7)
                - max_tokens: Maximum tokens to generate (default: 2048)
            analyzed_repository: Pre-analyzed repository with EVOLVE blocks
        """
        super().__init__(provider, memory, config, analyzed_repository)
        self.prompt_template = config.get("prompt_template", CROSSOVER_ALGORITHM)
        provider_cfg = getattr(provider, 'config', None) or {}
        self.temperature = self._get_config_value("temperature", config, provider_cfg, default=0.7)
        self.max_tokens = self._get_config_value("max_tokens", config, provider_cfg, default=4096)

    @property
    def n_parents(self) -> int:
        """Crossover sampler requires exactly 2 parents."""
        return 2

    @property
    def name(self) -> str:
        """Get the sampler name."""
        return "crossover_sampler"

    async def sample(
        self,
        parents: list[Algorithm],
        generation: int,
        **kwargs,
    ) -> Algorithm:
        """Sample a child algorithm from two parents via crossover.

        Takes two parent algorithms from the population and generates a child
        that combines their best ideas. Parents should be passed in kwargs.

        Args:
            parents: List of parent algorithms to use for crossover (at least 2)
            population: Current population of algorithms
            generation: Current generation number
            **kwargs: Must contain:
                - parents: List of parent algorithms (should have at least 2)
                - parent1: First parent (alternative to parents)
                - parent2: Second parent (alternative to parents)

        Returns:
            New child algorithm containing the combined insight
        """
        if len(parents) < 2:
            raise ValueError("CrossoverSampler requires at least two parent algorithms")
        if parents[0].id == parents[1].id:
            raise ValueError("CrossoverSampler requires two distinct parent algorithms")

        parent1, parent2 = parents[0], parents[1]

        # Get parent scores from evaluation
        score1 = parent1.evaluation.score if parent1.evaluation else 0.0
        score2 = parent2.evaluation.score if parent2.evaluation else 0.0

        # Get problem background
        background: str = kwargs.get("background", "")

        # Build memory context
        memory_context = ""
        if self.memory and not bool(kwargs.get("disable_memory", False)):
            memory_context = await self.memory.aget_prompt_context(
                query=background,
                context={
                    "sampler": "crossover",
                    "generation": generation,
                    "island_id": kwargs.get("island_id"),
                    "island_strategy": kwargs.get("island_strategy"),
                    "parent_1_score": score1,
                    "parent_1_description": parent1.description,
                    "parent_2_score": score2,
                    "parent_2_description": parent2.description,
                },
            )

        # Track generation time
        start_time = time.time()

        # Text-only crossover prompt
        prompt = self.prompt_template.format(
            background=background,
            memory_context=memory_context,
            score1=score1,
            description1=parent1.description,
            parent_context1=format_parent_algorithm_context(parent1),
            score2=score2,
            description2=parent2.description,
            parent_context2=format_parent_algorithm_context(parent2),
        )

        class AlgorithmSchema(BaseModel):
            name: str = Field(..., description="Concise name for the child algorithm")
            description: str = Field(..., description="Detailed description of the combined algorithm")

        response = await self.provider.generate(
            prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            schema=AlgorithmSchema,
            request_stage="planner",
        )

        generation_time_ms = (time.time() - start_time) * 1000
        total_prompt_tokens = response.prompt_tokens
        total_completion_tokens = response.completion_tokens

        if not response.parsed:
            raise ValueError(
                "LLM response could not be parsed into the expected schema for crossover generation."
            )

        algorithm_schema: AlgorithmSchema = response.parsed  # type: ignore

        # Create the new child algorithm
        algorithm = Algorithm(
            insight_type=InsightType.CROSSOVER,
            description=algorithm_schema.description,
            name=algorithm_schema.name,
            parent_ids=[p.id for p in parents],
            generation=generation,
        )

        # Fill in generation metadata
        algorithm.generation_meta = GenerationMetadata(
            operator=self.name,
            llm_provider=self.provider.__class__.__name__,
            llm_model=getattr(self.provider, "model", "unknown"),
            temperature=self.temperature,
            generation_time_ms=generation_time_ms,
            tokens_used=total_prompt_tokens + total_completion_tokens,
            agent_name="CrossoverSampler",
            operation_params={
                "parent_1_id": parent1.id,
                "parent_1_score": score1,
                "parent_1_description": parent1.description,
                "parent_2_id": parent2.id,
                "parent_2_score": score2,
                "parent_2_description": parent2.description,
            },
            change_description=algorithm.description,
            targeted_files=[],
        )

        return algorithm
