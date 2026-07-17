"""Mutation sampler that generates algorithm mutations from a parent algorithm.

This sampler takes an existing algorithm as input and generates a mutated version
by applying a small, targeted change. Uses the MUTATE_ALGORITHM_FROM_BLOCK template
to guide the LLM in generating meaningful mutations.
"""

import time
from typing import Any

from pydantic import BaseModel, Field

from llm4ad.infra.provider.base import BaseProvider
from llm4ad.infra.repo_analyzer.base import AnalyzedRepository, EvolveBlock
from llm4ad.planner.base import Algorithm, GenerationMetadata, InsightType
from llm4ad.planner.memory import Memory
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.prompt_templates import MUTATE_ALGORITHM_FROM_BLOCK


@BaseSampler.register("mutation_sampler")
class MutationSampler(BaseSampler):
    """Mutation sampler that generates mutated algorithm insights from a parent.

    Takes an existing algorithm (parent) and generates a new algorithm with
    targeted modifications. The mutation is guided by the MUTATE_ALGORITHM_FROM_BLOCK
    prompt template which focuses on making incremental improvements.
    """

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository | None = None,
    ):
        """Initialize the MutationSampler.

        Args:
            provider: LLM provider for generating mutations
            memory: Memory system for storing/retrieving past experiences
            config: Sampler configuration:
                - prompt_template: Custom prompt template to use (optional)
                - temperature: Sampling temperature (default: 0.7)
                - max_tokens: Maximum tokens to generate (default: 2048)
            analyzed_repository: Pre-analyzed repository with EVOLVE blocks
        """
        super().__init__(provider, memory, config, analyzed_repository)
        self.prompt_template = config.get("prompt_template", MUTATE_ALGORITHM_FROM_BLOCK)
        provider_cfg = getattr(provider, 'config', None) or {}
        self.temperature = self._get_config_value("temperature", config, provider_cfg, default=0.7)
        self.max_tokens = self._get_config_value("max_tokens", config, provider_cfg, default=4096)

    @property
    def n_parents(self) -> int:
        """Mutation sampler requires exactly 1 parent."""
        return 1

    @property
    def name(self) -> str:
        """Get the sampler name."""
        return "mutation_sampler"

    async def sample(
        self,
        parents: list[Algorithm],
        generation: int,
        **kwargs,
    ) -> Algorithm:
        """Sample a mutated algorithm from a parent.

        Takes a parent algorithm from the population and generates a mutation
        based on it. The parent should be passed in kwargs as 'parent'.

        Args:
            parents: Current population of algorithms
            generation: Current generation number
            **kwargs: Must contain:
                - parent: Parent algorithm to mutate
                - analyzed_repository: AnalyzedRepository (optional)

        Returns:
            New mutated algorithm containing the mutation insight
        """
        if parents is None or len(parents) < 1:
            raise ValueError("MutationSampler requires a 'parent' algorithm in kwargs")

        parent = parents[0]

        # Get the evolvable block to mutate
        block: EvolveBlock | None = None
        if self.analyzed_repository and len(self.analyzed_repository.evolvable_blocks) > 0:
            block = self.analyzed_repository.evolvable_blocks[0]

        # Get problem background
        background: str = kwargs.get("background", "")

        # Get parent score from evaluation
        parent_score = parent.evaluation.score if parent.evaluation else 0.0

        # Build memory context
        memory_context = await self.memory.aget_prompt_context(
            query=background,
            context={
                "sampler": "mutation",
                "parent_score": parent_score,
                "parent_description": parent.description,
            },
        ) if self.memory else ""

        # Track generation time
        start_time = time.time()

        class AlgorithmSchema(BaseModel):
            name: str = Field(..., description="Concise name for the mutated algorithm")
            description: str = Field(..., description="Detailed description of the mutation")

        # Text-only mutation prompt
        if block:
            prompt = self.prompt_template.format(
                background=background,
                memory_context=memory_context,
                file_path=block.file_path,
                language=block.language,
                block_name=block.block_name,
                context_before=block.context_before,
                current_content=block.original_content,
                context_after=block.context_after,
                score=parent_score,
                description=parent.description,
            )
        else:
            prompt = self._build_mutation_prompt_from_parent(background, parent, memory_context)

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
            raise ValueError("LLM response could not be parsed into the expected schema for mutation generation.")

        algorithm_schema: AlgorithmSchema = response.parsed  # type: ignore

        # Create the new mutated algorithm
        algorithm = Algorithm(
            insight_type=InsightType.MUTATION,
            description=algorithm_schema.description,
            name=algorithm_schema.name,
            parent_ids=[parent.id],
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
            agent_name="MutationSampler",
            operation_params={
                "parent_id": parent.id,
                "parent_score": parent_score,
                "parent_description": parent.description,
            },
            change_description=algorithm.description,
            targeted_files=[block.file_path] if block else [],
        )

        return algorithm

    def _build_mutation_prompt_from_parent(
        self, background: str, parent: Algorithm, memory_context: str = ""
    ) -> str:
        """Build a mutation prompt when no evolvable block is available.

        Args:
            background: Problem background
            parent: Parent algorithm to mutate
            memory_context: Formatted memory context string

        Returns:
            Formatted prompt string
        """
        parent_score = parent.evaluation.score if parent.evaluation else 0.0
        memory_section = (
            f"\n# Accumulated Knowledge\n\n{memory_context}\n" if memory_context else ""
        )
        return f"""\
You are an expert algorithm designer working to improve an existing algorithm through mutation.

# Problem Background

{background}
{memory_section}
# Current Algorithm Information

We have an existing algorithm in our population that we want to mutate.
Current algorithm score: {parent_score:.4f}
Current algorithm description: {parent.description}

# Your Task

Please propose a mutation (small change) to improve this algorithm.
Focus on making an incremental improvement based on the existing implementation.

Provide your response as a natural language description of the mutation, including:
1. The specific change you propose
2. Why you expect this change to improve performance
3. How your change integrates with the existing structure

Be specific about the algorithmic changes you propose.
"""
