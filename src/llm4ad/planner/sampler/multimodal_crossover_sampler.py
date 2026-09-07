"""Multimodal crossover sampler that includes behavior visualizations in prompts.

This sampler extends the crossover concept by embedding both parents' behavior
images into the LLM prompt, allowing the model to visually compare algorithm
behaviors and combine the best aspects of each.

Register this sampler in your YAML config to enable visual-grounded crossover:

    planner:
      samplers:
        - name: "multimodal_crossover_sampler"
"""

import time
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from llm4ad.infra.provider.base import BaseProvider, ChatMessage
from llm4ad.infra.repo_analyzer.base import AnalyzedRepository
from llm4ad.planner.base import Algorithm, GenerationMetadata, InsightType
from llm4ad.planner.memory import Memory
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.prompt_templates import (
    CROSSOVER_ALGORITHM,
    build_multimodal_crossover_prompt,
    format_parent_algorithm_context,
)


@BaseSampler.register("multimodal_crossover_sampler")
class MultimodalCrossoverSampler(BaseSampler):
    """Crossover sampler that includes behavior visualizations from both parents.

    When both parent algorithms have behavior data, this sampler builds a
    multimodal prompt so the LLM can visually compare their behaviors and
    combine the best aspects into a new child algorithm.

    If either parent lacks behavior data, falls back gracefully to text-only.
    """

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository | None = None,
    ):
        """Initialize the MultimodalCrossoverSampler.

        Args:
            provider: LLM provider (must support vision for multimodal).
            memory: Memory system for past experiences.
            config: Sampler configuration. Reads ``multimodal`` sub-dict
                for image limits and observation text settings.
            analyzed_repository: Pre-analyzed repository with EVOLVE blocks.
        """
        super().__init__(provider, memory, config, analyzed_repository)
        self.prompt_template = config.get("prompt_template", CROSSOVER_ALGORITHM)
        provider_cfg = getattr(provider, 'config', None) or {}
        self.temperature = self._get_config_value("temperature", config, provider_cfg, default=0.7)
        self.max_tokens = self._get_config_value("max_tokens", config, provider_cfg, default=4096)
        self.multimodal_config = config.get("multimodal", {})

    @property
    def n_parents(self) -> int:
        """Crossover requires exactly 2 parents."""
        return 2

    @property
    def name(self) -> str:
        """Get the sampler name."""
        return "multimodal_crossover_sampler"

    async def sample(
        self,
        parents: list[Algorithm],
        generation: int,
        **kwargs,
    ) -> Algorithm:
        """Sample a child algorithm from two parents, using behavior images when available.

        Args:
            parents: Parent algorithms (at least 2 required for crossover).
            generation: Current generation number.
            **kwargs: Optionally ``background`` (problem description).

        Returns:
            New child Algorithm combining both parents.
        """
        if len(parents) < 2:
            raise ValueError(
                "MultimodalCrossoverSampler requires at least two parent algorithms",
            )
        parent1, parent2 = parents[0], parents[1]
        score1 = parent1.evaluation.score if parent1.evaluation else 0.0
        score2 = parent2.evaluation.score if parent2.evaluation else 0.0
        background: str = kwargs.get("background", "")

        start_time = time.time()

        class AlgorithmSchema(BaseModel):
            behavior_analysis: str = Field(
                ...,
                description=(
                    "Comparative analysis of both parents' behavior based on the "
                    "provided visualization images. Describe what you observe in "
                    "each parent (e.g., trajectory patterns, strengths, weaknesses) "
                    "and how it informs the crossover strategy."
                ),
            )
            name: str = Field(..., description="Concise name for the child algorithm")
            description: str = Field(..., description="Detailed description of the combined algorithm")

        if parent1.has_behavior() and parent2.has_behavior():
            # Multimodal path: include behavior visualizations from both parents
            content_parts = build_multimodal_crossover_prompt(
                background=background,
                parent1=parent1,
                parent2=parent2,
                multimodal_config=self.multimodal_config,
            )
            response = await self.provider.chat(
                [ChatMessage(role="user", content=content_parts)],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                schema=AlgorithmSchema,
            )
        else:
            # Graceful fallback: one or both parents lack behavior data
            logger.debug(
                "One or both parents lack behavior data, "
                "falling back to text-only crossover"
            )
            prompt = self.prompt_template.format(
                background=background,
                memory_context="",
                score1=score1,
                description1=parent1.description,
                parent_context1=format_parent_algorithm_context(parent1),
                score2=score2,
                description2=parent2.description,
                parent_context2=format_parent_algorithm_context(parent2),
            )
            response = await self.provider.generate(
                prompt,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                schema=AlgorithmSchema,
            )

        generation_time_ms = (time.time() - start_time) * 1000

        if not response.parsed:
            raise ValueError(
                "LLM response could not be parsed into the expected schema "
                "for multimodal crossover generation."
            )

        algorithm_schema: AlgorithmSchema = response.parsed  # type: ignore

        behavior_analysis = getattr(algorithm_schema, "behavior_analysis", "")
        if behavior_analysis:
            logger.info(
                "Multimodal crossover behavior analysis:\n{}",
                behavior_analysis,
            )

        algorithm = Algorithm(
            insight_type=InsightType.CROSSOVER,
            description=algorithm_schema.description,
            name=algorithm_schema.name,
            parent_ids=[p.id for p in parents],
            generation=max(p.generation for p in parents) + 1,
        )

        algorithm.generation_meta = GenerationMetadata(
            operator="multimodal_crossover_sampler",
            llm_provider=self.provider.__class__.__name__,
            llm_model=getattr(self.provider, "model", "unknown"),
            temperature=self.temperature,
            generation_time_ms=generation_time_ms,
            tokens_used=response.prompt_tokens + response.completion_tokens,
            agent_name="MultimodalCrossoverSampler",
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
