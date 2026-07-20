"""ReEvo crossover sampler with short-term reflection.

Migrated from legacy LLM4AD ReEvo method. The crossover operator first
generates a short-term reflection by comparing a good and a bad parent, then
uses that reflection to guide the crossover. The reflection is stored in
``custom_metadata`` so a ReEvo-aware planner can aggregate it into long-term
reflection.
"""

from __future__ import annotations

from typing import Any

from llm4ad.planner.base import Algorithm, InsightType
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.eoh_i1_sampler import _BaseEoHSampler


@BaseSampler.register("reevo_crossover_sampler")
class ReEvoCrossoverSampler(_BaseEoHSampler):
    """ReEvo crossover sampler with short-term reflection.

    Compares two parents (better + worse) to generate a short reflection, then
    uses that reflection to guide the crossover.
    """

    @property
    def n_parents(self) -> int:
        """Number of parent algorithms required (2 for crossover)."""
        return 2

    @property
    def name(self) -> str:
        """Get the sampler name."""
        return "reevo_crossover_sampler"

    async def sample(
        self,
        parents: list[Algorithm],
        generation: int,
        **kwargs: Any,
    ) -> Algorithm:
        """Generate a crossover guided by short-term reflection.

        Args:
            parents: List of 2 parents.
            generation: Current generation number.
            **kwargs: Additional parameters (background, template_function).

        Returns:
            Algorithm whose insight is guided by the short-term reflection.
        """
        if len(parents) != 2:
            raise ValueError(f"ReEvo crossover requires 2 parents, got {len(parents)}")

        # Order parents by score: better first.
        ordered = sorted(parents, key=lambda p: p.score, reverse=True)
        good_parent, bad_parent = ordered[0], ordered[1]

        background = kwargs.get("background", "")
        template_text = self._template_text(kwargs)

        # Step 1: short-term reflection (a separate, un-schema'd LLM call).
        reflection = await self._short_term_reflection(background, good_parent, bad_parent)

        # Step 2: crossover guided by the reflection.
        prompt = (
            "You are an expert in optimization heuristics.\n"
            f"{background}\n"
            "[Reflection on why the better algorithm wins]\n"
            f"{reflection}\n"
            "[Better algorithm]\n"
            f"{good_parent.description}\n{self._get_parent_code(good_parent)}\n"
            "[Worse algorithm]\n"
            f"{bad_parent.description}\n{self._get_parent_code(bad_parent)}\n"
            "Please design a new algorithm that combines the strengths of both, "
            "guided by the reflection above, for the following function:\n"
            f"{template_text}\n"
            "Return a concise algorithm name and a one-sentence description."
        )

        algorithm = await self._generate(
            prompt,
            generation=generation,
            insight_type=InsightType.CROSSOVER,
            operator="reevo_crossover",
            parent_ids=[good_parent.id, bad_parent.id],
        )
        # Persist the reflection so a ReEvo-aware planner can aggregate it.
        algorithm.custom_metadata["short_term_reflection"] = reflection
        return algorithm

    async def _short_term_reflection(
        self, background: str, good_parent: Algorithm, bad_parent: Algorithm
    ) -> str:
        """Generate a short reflection comparing the better vs worse parent.

        Args:
            background: Task background.
            good_parent: Better-performing parent.
            bad_parent: Worse-performing parent.

        Returns:
            A short reflection string (truncated to ~25 words).
        """
        prompt = (
            "You are an expert in optimization heuristics. Provide concise "
            "insights (under 20 words).\n"
            f"{background}\n"
            f"[Better algorithm, score={good_parent.score:.4f}]\n"
            f"{self._get_parent_code(good_parent)}\n"
            f"[Worse algorithm, score={bad_parent.score:.4f}]\n"
            f"{self._get_parent_code(bad_parent)}\n"
            "In one sentence (under 20 words), explain why the better algorithm "
            "outperforms the worse one."
        )

        response = await self.provider.generate(
            prompt,
            temperature=min(self.temperature, 0.5),
            max_tokens=128,
            request_stage="planner",
        )
        reflection = (response.text or "").strip()
        words = reflection.split()
        if len(words) > 25:
            reflection = " ".join(words[:25]) + "..."
        return reflection or "The better algorithm balances cost and feasibility more effectively."
