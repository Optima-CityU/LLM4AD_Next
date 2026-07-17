"""EoH I1 sampler: initial population generation.

Migrated from legacy LLM4AD EoH method (llm4ad/method/eoh/prompt.py).
The I1 operator generates an initial algorithm insight from the task
description and template function, with no parent algorithms for reference.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from llm4ad.infra.provider.base import BaseProvider
from llm4ad.infra.repo_analyzer.base import AnalyzedRepository
from llm4ad.infra.timing import ExecutionTiming
from llm4ad.planner.base import Algorithm, GenerationMetadata, InsightType
from llm4ad.planner.memory import Memory
from llm4ad.planner.sampler.base import BaseSampler


class EoHAlgorithmSchema(BaseModel):
    """Structured response for EoH insight generation.

    The legacy EoH prompt asks the LLM to describe the algorithm in one
    sentence inside boxed ``{}`` and then implement the function. Here we ask
    the model to return a structured name + description; the coder generates
    the actual code in a later stage.
    """

    name: str | None = Field(default=None, description="Concise algorithm name")
    description: str = Field(..., description="One-sentence algorithm description")


class _BaseEoHSampler(BaseSampler):
    """Shared helper for EoH samplers.

    Provides temperature/max_tokens resolution, EVOLVE-block access, parent
    code extraction, and a common LLM-call + Algorithm-construction routine so
    each operator only needs to build its prompt.
    """

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository | None = None,
    ) -> None:
        """Initialize the base EoH sampler.

        Args:
            provider: LLM provider for generating insights.
            memory: Memory system (unused by EoH operators).
            config: Sampler configuration dict.
            analyzed_repository: Analyzed repository with EVOLVE blocks.
        """
        super().__init__(provider, memory, config, analyzed_repository)
        provider_cfg = getattr(provider, "config", None) or {}
        self.temperature = self._get_config_value(
            "temperature", config, provider_cfg, default=0.7
        )
        self.max_tokens = self._get_config_value(
            "max_tokens", config, provider_cfg, default=4096
        )

    def _first_block(self):
        """Return the first EVOLVE block if available, else None."""
        if self.analyzed_repository and self.analyzed_repository.evolvable_blocks:
            return self.analyzed_repository.evolvable_blocks[0]
        return None

    def _template_text(self, kwargs: dict[str, Any]) -> str:
        """Resolve the function template text for the prompt.

        Prefers an explicit ``template_function`` kwarg, then falls back to the
        first EVOLVE block's content, then to an empty placeholder.
        """
        template = kwargs.get("template_function", "")
        if template:
            return template
        block = self._first_block()
        if block is not None:
            return block.original_content
        return "# Implement the target function."

    @staticmethod
    def _get_parent_code(parent: Algorithm) -> str:
        """Extract a parent's code from artifacts or generation metadata.

        Args:
            parent: Parent algorithm.

        Returns:
            Parent code as a string, or a placeholder when unavailable.
        """
        if parent.code_artifacts:
            for artifact in parent.code_artifacts:
                if artifact.is_entrypoint:
                    return artifact.content
            return parent.code_artifacts[0].content
        # Fall back to any raw response captured in custom_metadata.
        raw = parent.custom_metadata.get("full_response", "") if parent.custom_metadata else ""
        if raw:
            return raw
        return "# Code not available"

    @staticmethod
    def _derive_name(description: str, operator: str) -> str:
        """Derive a fallback algorithm name from the description."""
        words = [
            word.strip(" ,.:;()[]{}")
            for word in description.split()
            if word.strip(" ,.:;()[]{}")
        ]
        if not words:
            return f"EoH_{operator.upper()}"
        return " ".join(words[:6])

    async def _generate(
        self,
        prompt: str,
        *,
        generation: int,
        insight_type: InsightType,
        operator: str,
        parent_ids: list[str],
    ) -> Algorithm:
        """Call the LLM and build an Algorithm from the response.

        Args:
            prompt: Fully formatted prompt string.
            generation: Current generation number.
            insight_type: Insight type for the new algorithm.
            operator: Operator name (e.g. ``i1``, ``e1``).
            parent_ids: IDs of parent algorithms.

        Returns:
            New Algorithm with description and generation metadata.

        Raises:
            ValueError: If the model output cannot be parsed.
        """
        start_time = time.time()
        response = await self.provider.generate(
            prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            schema=EoHAlgorithmSchema,
            request_stage="planner",
        )

        payload: EoHAlgorithmSchema | None = response.parsed  # type: ignore[assignment]
        if payload is None:
            # Fallback: derive a description from the raw boxed text.
            description = self._extract_boxed_description(response.text or "")
            payload = EoHAlgorithmSchema(name=None, description=description)

        algorithm_name = payload.name or self._derive_name(payload.description, operator)
        algorithm = Algorithm(
            insight_type=insight_type,
            name=algorithm_name,
            description=payload.description,
            generation=generation,
            parent_ids=parent_ids,
        )
        algorithm.update_timing(
            response.timing
            or ExecutionTiming.from_llm_stage("planner", (time.time() - start_time) * 1000)
        )
        algorithm.generation_meta = GenerationMetadata(
            operator=operator,
            llm_provider=self.provider.__class__.__name__,
            llm_model=getattr(self.provider, "model", "unknown"),
            temperature=self.temperature,
            generation_time_ms=(time.time() - start_time) * 1000,
            tokens_used=response.total_tokens,
            agent_name=self.__class__.__name__,
            change_description=payload.description,
            targeted_files=[self._first_block().file_path] if self._first_block() else [],
        )
        algorithm.custom_metadata["eoh_operator"] = operator
        if response.text:
            algorithm.custom_metadata["full_response"] = response.text
        return algorithm

    @staticmethod
    def _extract_boxed_description(text: str) -> str:
        """Extract the algorithm description from boxed ``{...}`` markers.

        Args:
            text: Full LLM response text.

        Returns:
            Extracted description, or the first non-empty line if no box found.
        """
        start = text.find("{")
        end = text.find("}", start)
        if start != -1 and end != -1 and end > start:
            inner = text[start + 1 : end].strip()
            if inner:
                return inner
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return stripped[:200]
        return "Initial algorithm"


@BaseSampler.register("eoh_i1_sampler")
class EoHI1Sampler(_BaseEoHSampler):
    """EoH I1 sampler for initial population generation.

    Generates initial algorithm insights with no parent context, only the task
    description and template function signature.
    """

    @property
    def n_parents(self) -> int:
        """Number of parent algorithms required (0 for initial generation)."""
        return 0

    @property
    def name(self) -> str:
        """Get the sampler name."""
        return "eoh_i1_sampler"

    async def sample(
        self,
        parents: list[Algorithm],
        generation: int,
        **kwargs: Any,
    ) -> Algorithm:
        """Generate an initial algorithm insight.

        Args:
            parents: Empty list (I1 has no parents).
            generation: Current generation number (should be 0).
            **kwargs: Additional parameters (background, template_function).

        Returns:
            Algorithm with initial insight description.
        """
        background = kwargs.get("background", "")
        template_text = self._template_text(kwargs)

        prompt = (
            f"{background}\n"
            "1. First, describe your new algorithm and main steps in one sentence.\n"
            "2. Next, design a Python implementation for the following function:\n"
            f"{template_text}\n"
            "Return a concise algorithm name and a one-sentence description of "
            "your approach."
        )

        return await self._generate(
            prompt,
            generation=generation,
            insight_type=InsightType.INITIAL,
            operator="i1",
            parent_ids=[],
        )
