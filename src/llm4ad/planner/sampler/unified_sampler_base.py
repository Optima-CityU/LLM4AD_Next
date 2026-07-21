"""Neutral base class for "unified" samplers (thought + code in one call).

This is method-agnostic infrastructure shared by EoH, MEoH, ReEvo, and
MCTS-AHD samplers. It owns the LLM call, structured-output schemas, the
best-effort JSON fallback parser, temperature/token resolution, and the
EVOLVE-block accessor. Method-specific samplers subclass ``BaseUnifiedSampler``
and only supply their own prompts and operator wiring.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from llm4ad.infra.provider.base import BaseProvider
from llm4ad.infra.repo_analyzer.base import AnalyzedRepository
from llm4ad.infra.timing import ExecutionTiming
from llm4ad.planner.base import Algorithm, GenerationMetadata, InsightType
from llm4ad.planner.memory import Memory
from llm4ad.planner.sampler.base import BaseSampler


class UnifiedAlgorithmSchema(BaseModel):
    """Structured response for description-only insight generation."""

    name: str | None = Field(default=None, description="Concise algorithm name")
    description: str = Field(..., description="Detailed algorithm description")


class UnifiedResultSchema(BaseModel):
    """Schema for unified generation (thought + code in one call)."""

    name: str | None = Field(default=None, description="Concise algorithm name")
    description: str = Field(..., description="Algorithm description and main steps")
    code: str = Field(..., description="Complete implementation code for the EVOLVE block")


class BaseUnifiedSampler(BaseSampler):
    """Shared helper for unified (thought + code) samplers.

    Subclasses supply method-specific prompts and operator names; this base
    handles the LLM interaction and result parsing.
    """

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository,
    ) -> None:
        """Initialize the sampler and resolve generation parameters.

        Args:
            provider: LLM provider used for generation.
            memory: Memory system (unused by most unified samplers).
            config: Sampler/planner configuration mapping.
            analyzed_repository: Pre-analyzed repository with EVOLVE blocks.
        """
        super().__init__(provider, memory, config, analyzed_repository)
        provider_cfg = getattr(provider, "config", None) or {}
        self.temperature = self._get_config_value("temperature", config, provider_cfg, default=0.7)
        self.max_tokens = self._get_config_value("max_tokens", config, provider_cfg, default=4096)

    def _first_block(self):
        """Return the first evolvable block, or None when unavailable."""
        if self.analyzed_repository and self.analyzed_repository.evolvable_blocks:
            return self.analyzed_repository.evolvable_blocks[0]
        return None

    @staticmethod
    def _fallback_parse_unified(raw_text: str) -> UnifiedResultSchema | None:
        """Best-effort parser when the LLM emits malformed JSON.

        Handles raw (unescaped) newlines inside the ``code`` field and other
        common JSON-formatting mistakes.

        Args:
            raw_text: The raw model output.

        Returns:
            A parsed ``UnifiedResultSchema`` or None if all attempts fail.
        """
        if not raw_text:
            return None

        # Strip markdown fences
        text = raw_text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[^\n]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
            text = text.strip()

        # Attempt 1: escape literal newlines inside quoted JSON strings.
        try:
            fixed = re.sub(
                r'("(?:[^"\\]|\\.)*")',
                lambda m: m.group(0).replace("\n", "\\n"),
                text,
                flags=re.DOTALL,
            )
            data = json.loads(fixed)
            return UnifiedResultSchema(
                name=data.get("name"),
                description=data.get("description", ""),
                code=data.get("code", ""),
            )
        except Exception:
            pass

        # Attempt 2: regex-extract each field individually
        try:
            name_m = re.search(r'"name"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
            desc_m = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
            code_m = re.search(r'"code"\s*:\s*"(.*?)"\s*\}', text, re.DOTALL)

            if desc_m and code_m:
                name = name_m.group(1) if name_m else None
                description = desc_m.group(1).replace("\\n", "\n").replace('\\"', '"')
                code = code_m.group(1).replace("\\n", "\n").replace('\\"', '"')
                return UnifiedResultSchema(name=name, description=description, code=code)
        except Exception:
            pass

        # Attempt 3: extract a markdown code block as the code
        try:
            code_block_m = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
            if code_block_m:
                code = code_block_m.group(1).strip()
                before = text[: code_block_m.start()].strip()
                name_m = re.search(r'"name"\s*:\s*"([^"]+)"', before)
                desc_m = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"', before)
                description = desc_m.group(1) if desc_m else before[:200]
                name = name_m.group(1) if name_m else None
                return UnifiedResultSchema(name=name, description=description, code=code)
        except Exception:
            pass

        return None

    async def _generate_unified(
        self,
        prompt: str,
        *,
        generation: int,
        insight_type: InsightType,
        operator: str,
        parent_ids: list[str],
    ) -> Algorithm:
        """Generate algorithm description and code in a single LLM call.

        Args:
            prompt: The fully-composed prompt.
            generation: Current generation number.
            insight_type: Insight type to tag the algorithm with.
            operator: Operator label (e.g. ``i1``/``e1``/``crossover``).
            parent_ids: Parent algorithm ids for lineage tracking.

        Returns:
            The generated ``Algorithm`` with unified code stashed in metadata.

        Raises:
            ValueError: If the model output cannot be parsed.
        """
        start_time = time.time()
        response = await self.provider.generate(
            prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            schema=UnifiedResultSchema,
            request_stage="planner",
        )
        payload: UnifiedResultSchema | None = response.parsed  # type: ignore[assignment]

        if payload is None and response.text:
            payload = self._fallback_parse_unified(response.text)
            if payload is not None:
                logger.debug(f"Unified sampler {operator}: used fallback JSON parser")

        if payload is None:
            raise ValueError(f"Unified sampler {operator} failed to parse model output")

        algorithm_name = payload.name or self._derive_name(payload.description, operator)
        algorithm = Algorithm(
            insight_type=insight_type,
            name=algorithm_name,
            description=payload.description,
            generation=generation,
            parent_ids=parent_ids,
        )
        algorithm.custom_metadata["unified_code"] = payload.code
        algorithm.update_timing(
            response.timing or ExecutionTiming.from_llm_stage("planner", (time.time() - start_time) * 1000)
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
        algorithm.custom_metadata["operator"] = operator
        return algorithm

    @staticmethod
    def _derive_name(description: str, operator: str) -> str:
        """Derive a fallback algorithm name from the description.

        Args:
            description: Algorithm description text.
            operator: Operator label used for the fallback name.

        Returns:
            A short derived name.
        """
        words = [word.strip(" ,.:;()[]{}") for word in description.split() if word.strip(" ,.:;()[]{}")]
        if not words:
            return f"ALG_{operator.upper()}"
        return " ".join(words[:6])
