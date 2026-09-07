"""Multimodal mutation sampler that includes behavior visualizations in prompts.

This sampler extends the mutation concept by embedding parent algorithm behavior
images (trajectory plots, tour visualizations, etc.) into the LLM prompt,
allowing the model to visually diagnose behavior and propose targeted mutations.

Register this sampler in your YAML config to enable visual-grounded evolution:

    planner:
      samplers:
        - name: "multimodal_mutation_sampler"
"""

import time
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from llm4ad.infra.provider.base import BaseProvider, ChatMessage
from llm4ad.infra.repo_analyzer.base import AnalyzedRepository, EvolveBlock
from llm4ad.planner.base import Algorithm, GenerationMetadata, InsightType
from llm4ad.planner.memory import Memory
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.prompt_templates import (
    MUTATE_ALGORITHM_FROM_BLOCK,
    build_multimodal_mutation_prompt,
    format_parent_algorithm_context,
)


@BaseSampler.register("multimodal_mutation_sampler")
class MultimodalMutationSampler(BaseSampler):
    """Mutation sampler that includes behavior visualizations in LLM prompts.

    When the parent algorithm has behavior data (images + observations from
    evaluation), this sampler builds a multimodal prompt so the LLM can
    visually diagnose algorithm behavior and propose targeted improvements.

    If the parent lacks behavior data (e.g., initial generation before any
    multimodal evaluation), falls back gracefully to text-only prompting.
    """

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository | None = None,
    ):
        """Initialize the MultimodalMutationSampler.

        Args:
            provider: LLM provider (must support vision for multimodal).
            memory: Memory system for past experiences.
            config: Sampler configuration. Reads ``multimodal`` sub-dict
                for image limits and observation text settings.
            analyzed_repository: Pre-analyzed repository with EVOLVE blocks.
        """
        super().__init__(provider, memory, config, analyzed_repository)
        self.prompt_template = config.get("prompt_template", MUTATE_ALGORITHM_FROM_BLOCK)
        provider_cfg = getattr(provider, 'config', None) or {}
        self.temperature = self._get_config_value("temperature", config, provider_cfg, default=0.7)
        self.max_tokens = self._get_config_value("max_tokens", config, provider_cfg, default=4096)
        self.multimodal_config = config.get("multimodal", {})

    @property
    def n_parents(self) -> int:
        """Mutation requires exactly 1 parent."""
        return 1

    @property
    def name(self) -> str:
        """Get the sampler name."""
        return "multimodal_mutation_sampler"

    async def sample(
        self,
        parents: list[Algorithm],
        generation: int,
        **kwargs,
    ) -> Algorithm:
        """Sample a mutated algorithm, using behavior images when available.

        Args:
            parents: Parent algorithms (first is used as the mutation parent).
            generation: Current generation number.
            **kwargs: Optionally ``background`` (problem description).

        Returns:
            New mutated Algorithm.
        """
        if parents is None or len(parents) < 1:
            raise ValueError("MultimodalMutationSampler requires at least one parent algorithm")

        parent = parents[0]
        block: EvolveBlock | None = None
        if self.analyzed_repository and len(self.analyzed_repository.evolvable_blocks) > 0:
            block = self.analyzed_repository.evolvable_blocks[0]

        background: str = kwargs.get("background", "")
        parent_score = parent.evaluation.score if parent.evaluation else 0.0

        start_time = time.time()

        class AlgorithmSchema(BaseModel):
            behavior_analysis: str = Field(
                ...,
                description=(
                    "Analysis of the parent algorithm's behavior based on the "
                    "provided visualization images. Describe what you observe "
                    "(e.g., trajectory patterns, inefficiencies, failure modes) "
                    "and how it informs your proposed mutation."
                ),
            )
            name: str = Field(..., description="Concise name for the mutated algorithm")
            description: str = Field(..., description="Detailed description of the mutation")

        if parent.has_behavior():
            # Multimodal path: include behavior visualizations in prompt
            content_parts = build_multimodal_mutation_prompt(
                background=background,
                parent=parent,
                block=block,
                multimodal_config=self.multimodal_config,
            )
            response = await self.provider.chat(
                [ChatMessage(role="user", content=content_parts)],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                schema=AlgorithmSchema,
            )
        else:
            # Graceful fallback: parent has no behavior data yet
            logger.debug(
                "Parent has no behavior data, falling back to text-only mutation"
            )
            if block:
                prompt = self.prompt_template.format(
                    background=background,
                    file_path=block.file_path,
                    language=block.language,
                    block_name=block.block_name,
                    context_before=block.context_before,
                    current_content=block.original_content,
                    context_after=block.context_after,
                    score=parent_score,
                    description=parent.description,
                    parent_context=format_parent_algorithm_context(parent),
                    memory_context="",
                )
            else:
                prompt = self._build_fallback_prompt(background, parent)

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
                "for multimodal mutation generation."
            )

        algorithm_schema: AlgorithmSchema = response.parsed  # type: ignore[assignment]

        behavior_analysis = getattr(algorithm_schema, "behavior_analysis", "")
        if behavior_analysis:
            logger.info(
                "Multimodal mutation behavior analysis:\n{}",
                behavior_analysis,
            )

        algorithm = Algorithm(
            insight_type=InsightType.MUTATION,
            description=algorithm_schema.description,
            name=algorithm_schema.name,
            parent_ids=[parent.id],
            generation=generation,
        )

        algorithm.generation_meta = GenerationMetadata(
            operator="multimodal_mutation_sampler",
            llm_provider=self.provider.__class__.__name__,
            llm_model=getattr(self.provider, "model", "unknown"),
            temperature=self.temperature,
            generation_time_ms=generation_time_ms,
            tokens_used=response.prompt_tokens + response.completion_tokens,
            agent_name="MultimodalMutationSampler",
            operation_params={
                "parent_id": parent.id,
                "parent_score": parent_score,
                "parent_description": parent.description,
            },
            change_description=algorithm.description,
            targeted_files=[block.file_path] if block else [],
        )

        return algorithm

    @staticmethod
    def _build_fallback_prompt(background: str, parent: Algorithm) -> str:
        """Build a text-only mutation prompt when no behavior data is available.

        Args:
            background: Problem background.
            parent: Parent algorithm to mutate.

        Returns:
            Formatted prompt string.
        """
        parent_score = parent.evaluation.score if parent.evaluation else 0.0
        return (
            "You are an expert algorithm designer working to improve an "
            "existing algorithm through mutation.\n\n"
            f"# Problem Background\n\n{background}\n\n"
            f"# Current Algorithm Information\n\n"
            f"Current algorithm score: {parent_score:.4f}\n"
            f"Current algorithm description: {parent.description}\n\n"
            f"# Selected Parent State and Implementation\n\n"
            f"{format_parent_algorithm_context(parent)}\n\n"
            "# Your Task\n\n"
            "Please propose a mutation (small change) to improve this algorithm.\n"
            "Focus on making an incremental improvement based on the existing "
            "implementation.\n"
        )
