"""ReEvo (Reflective Evolution) samplers.

Migrated from the legacy LLM4AD ``method/reevo``. ReEvo augments genetic
search with LLM reflection:

- **Short-term reflection**: compare a worse/better parent pair, ask the LLM
  for a concise hint, then use that hint to guide a crossover.
- **Long-term reflection**: periodically distil accumulated short-term hints
  (plus the prior long-term reflection) into constructive guidance that
  drives elite mutation.

These samplers reuse ``_BaseMEoHSampler._generate_unified`` for the final
thought+code generation, and add an extra reflection LLM call where needed.
The orchestrator threads the reflection state through ``kwargs``.
"""

from __future__ import annotations

from typing import Any

from llm4ad.infra.provider.base import BaseProvider
from llm4ad.infra.repo_analyzer.base import AnalyzedRepository
from llm4ad.planner.base import Algorithm, InsightType
from llm4ad.planner.memory import Memory
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.meoh_init_sampler import _BaseMEoHSampler
from llm4ad.planner.sampler.meoh_prompt_templates import (
    _UNIFIED_JSON_SUFFIX,
    format_block_context,
    format_parent_context,
)

_SYSTEM_HINT = (
    "You are an expert in the domain of optimization heuristics. "
    "Your task is to design heuristics that effectively solve the problem."
)


def build_short_term_reflection_prompt(background: str, worse: Algorithm, better: Algorithm) -> str:
    """Build the short-term reflection prompt (worse vs better parent).

    Args:
        background: Task background description.
        worse: The lower-scoring parent.
        better: The higher-scoring parent.

    Returns:
        Prompt asking for a concise design hint (< 30 words).
    """
    return (
        f"{_SYSTEM_HINT}\n\n"
        f"Task background:\n{background}\n\n"
        "Below are two algorithms for this task. The second performs better than the first.\n"
        f"[Worse algorithm]\nDescription: {worse.description}\nScore: {worse.score}\n\n"
        f"[Better algorithm]\nDescription: {better.description}\nScore: {better.score}\n\n"
        "Respond with concise hints (less than 30 words) for designing better heuristics, "
        "based on the difference between the two versions. Output only the hint text."
    )


def build_long_term_reflection_prompt(
    background: str, prior_long_term: str, new_short_terms: list[str]
) -> str:
    """Build the long-term reflection prompt.

    Args:
        background: Task background description.
        prior_long_term: The previous long-term reflection (may be empty).
        new_short_terms: Recently gathered short-term hints.

    Returns:
        Prompt asking for constructive long-term guidance (< 50 words).
    """
    joined = "\n".join(f"- {s}" for s in new_short_terms if s)
    return (
        f"{_SYSTEM_HINT}\n\n"
        f"Task background:\n{background}\n\n"
        f"Your prior long-term reflection on designing heuristics:\n{prior_long_term or '(none)'}\n\n"
        f"Newly gained insights:\n{joined or '(none)'}\n\n"
        "Write constructive hints for designing better heuristics, based on the prior "
        "reflection and the new insights, using less than 50 words. Output only the hint text."
    )


def build_reevo_init_prompt(background: str, block: Any) -> str:
    """Build the ReEvo population-initialization prompt.

    Args:
        background: Task background description.
        block: The EVOLVE block (or None).

    Returns:
        Unified thought+code init prompt.
    """
    return (
        f"{_SYSTEM_HINT}\n\n"
        f"{background}\n\n"
        f"Repository context (code surrounding the EVOLVE block):\n{format_block_context(block)}\n\n"
        "Propose an initial heuristic for the task above.\n"
        "1. First, describe your algorithm: name it and explain the main idea.\n"
        "2. Then, write the complete replacement code for the EVOLVE block. "
        "Your code must define the same function(s) that the code after EVOLVE_END calls.\n\n"
        f"{_UNIFIED_JSON_SUFFIX}"
    )


def build_reevo_crossover_prompt(
    background: str, block: Any, worse: Algorithm, better: Algorithm, reflection: str
) -> str:
    """Build the reflection-guided crossover prompt.

    Args:
        background: Task background description.
        block: The EVOLVE block (or None).
        worse: The lower-scoring parent.
        better: The higher-scoring parent.
        reflection: Short-term reflection hint.

    Returns:
        Unified thought+code crossover prompt guided by the reflection.
    """
    return (
        f"{_SYSTEM_HINT}\n\n"
        f"{background}\n\n"
        f"Repository context (code surrounding the EVOLVE block):\n{format_block_context(block)}\n\n"
        f"[Worse algorithm]\n{format_parent_context([worse])}\n\n"
        f"[Better algorithm]\n{format_parent_context([better])}\n\n"
        f"[Reflection / hint]\n{reflection}\n\n"
        "Write an improved algorithm that combines the strengths of both, guided by the "
        "reflection above.\n"
        "1. First, describe your new algorithm and main steps.\n"
        "2. Then, write the complete replacement code for the EVOLVE block. "
        "Your code must define the same function(s) that the code after EVOLVE_END calls.\n\n"
        f"{_UNIFIED_JSON_SUFFIX}"
    )


def build_reevo_mutation_prompt(
    background: str, block: Any, elite: Algorithm, long_term_reflection: str
) -> str:
    """Build the long-term-reflection-guided elite mutation prompt.

    Args:
        background: Task background description.
        block: The EVOLVE block (or None).
        elite: The elite algorithm to mutate.
        long_term_reflection: Accumulated long-term reflection.

    Returns:
        Unified thought+code mutation prompt guided by the long-term reflection.
    """
    return (
        f"{_SYSTEM_HINT}\n\n"
        f"{background}\n\n"
        f"Repository context (code surrounding the EVOLVE block):\n{format_block_context(block)}\n\n"
        f"[Prior long-term reflection]\n{long_term_reflection or '(none)'}\n\n"
        f"[Elite algorithm to improve]\n{format_parent_context([elite])}\n\n"
        "Write a mutated, improved version of the elite algorithm, guided by the reflection.\n"
        "1. First, describe your new algorithm and what you changed.\n"
        "2. Then, write the complete replacement code for the EVOLVE block. "
        "Your code must define the same function(s) that the code after EVOLVE_END calls.\n\n"
        f"{_UNIFIED_JSON_SUFFIX}"
    )


class _BaseReEvoSampler(_BaseMEoHSampler):
    """Shared helper: adds a plain-text reflection LLM call."""

    async def _reflect(self, prompt: str) -> str:
        """Run a single reflection LLM call and return the hint text.

        Args:
            prompt: Reflection prompt.

        Returns:
            Reflection hint text (empty string on failure).
        """
        response = await self.provider.generate(
            prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            request_stage="planner",
        )
        return (response.text or "").strip()


@BaseSampler.register("reevo_init_sampler")
class ReEvoInitSampler(_BaseReEvoSampler):
    """Generate an initial ReEvo algorithm."""

    @property
    def n_parents(self) -> int:
        """Initial sampler needs no parents."""
        return 0

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "reevo_init_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Sample an initial algorithm (thought + code)."""
        prompt = build_reevo_init_prompt(kwargs.get("background", ""), self._first_block())
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.INITIAL,
            operator="init",
            parent_ids=[],
        )


@BaseSampler.register("reevo_crossover_sampler")
class ReEvoCrossoverSampler(_BaseReEvoSampler):
    """Reflection-guided crossover: short-term reflect, then combine two parents."""

    def __init__(
        self,
        provider: BaseProvider,
        memory: Memory,
        config: dict[str, Any],
        analyzed_repository: AnalyzedRepository,
    ) -> None:
        """Initialize the crossover sampler."""
        super().__init__(provider, memory, config, analyzed_repository)

    @property
    def n_parents(self) -> int:
        """Crossover needs two parents."""
        return 2

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "reevo_crossover_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Reflect on a worse/better parent pair, then generate a crossover child."""
        parents = kwargs.get("parents", population)
        if len(parents) < 2:
            raise ValueError("ReEvo crossover requires two parents")
        background = kwargs.get("background", "")

        # Order parents by score (worse first) for the reflection prompt.
        ordered = sorted(parents[:2], key=lambda a: a.score if a.is_evaluated() else float("-inf"))
        worse, better = ordered[0], ordered[1]

        reflection = await self._reflect(
            build_short_term_reflection_prompt(background, worse, better)
        )

        prompt = build_reevo_crossover_prompt(background, self._first_block(), worse, better, reflection)
        algorithm = await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.CROSSOVER,
            operator="crossover",
            parent_ids=[worse.id, better.id],
        )
        # Stash the short-term reflection so the orchestrator can accumulate it.
        algorithm.custom_metadata["reevo_short_term_reflection"] = reflection
        return algorithm


@BaseSampler.register("reevo_mutation_sampler")
class ReEvoMutationSampler(_BaseReEvoSampler):
    """Long-term-reflection-guided elite mutation."""

    @property
    def n_parents(self) -> int:
        """Mutation needs one parent (the elite)."""
        return 1

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "reevo_mutation_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Mutate the elite algorithm guided by the long-term reflection."""
        parents = kwargs.get("parents", population)
        if not parents:
            raise ValueError("ReEvo mutation requires one parent (elite)")
        background = kwargs.get("background", "")
        long_term = kwargs.get("long_term_reflection", "")
        elite = parents[0]

        prompt = build_reevo_mutation_prompt(background, self._first_block(), elite, long_term)
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.MUTATION,
            operator="mutation",
            parent_ids=[elite.id],
        )
