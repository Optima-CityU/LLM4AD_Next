"""ReEvo mutation sampler with long-term reflection.

Migrated from legacy LLM4AD ReEvo method. The mutation operator uses
accumulated long-term reflection (passed via kwargs by a ReEvo-aware planner)
to guide elite mutation.
"""

from __future__ import annotations

from typing import Any

from llm4ad.planner.base import Algorithm, InsightType
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.eoh_i1_sampler import _BaseEoHSampler


@BaseSampler.register("reevo_mutation_sampler")
class ReEvoMutationSampler(_BaseEoHSampler):
    """ReEvo mutation sampler with long-term reflection.

    Mutates an elite parent using accumulated long-term reflection as guidance.
    The long-term reflection is supplied via the ``long_term_reflection``
    kwarg; when absent it falls back to an empty reflection.
    """

    @property
    def n_parents(self) -> int:
        """Number of parent algorithms required (1 for mutation)."""
        return 1

    @property
    def name(self) -> str:
        """Get the sampler name."""
        return "reevo_mutation_sampler"

    async def sample(
        self,
        parents: list[Algorithm],
        generation: int,
        **kwargs: Any,
    ) -> Algorithm:
        """Generate a mutation guided by long-term reflection.

        Args:
            parents: List containing 1 elite parent.
            generation: Current generation number.
            **kwargs: Additional parameters (background, template_function,
                long_term_reflection).

        Returns:
            Algorithm whose insight is guided by the long-term reflection.
        """
        if len(parents) != 1:
            raise ValueError(f"ReEvo mutation requires 1 parent, got {len(parents)}")

        parent = parents[0]
        background = kwargs.get("background", "")
        template_text = self._template_text(kwargs)
        long_term_reflection = kwargs.get("long_term_reflection", "")

        prompt = (
            "You are an expert in optimization heuristics.\n"
            f"{background}\n"
            "[Prior long-term reflection]\n"
            f"{long_term_reflection}\n"
            "[Current algorithm]\n"
            f"{parent.description}\n{self._get_parent_code(parent)}\n"
            "Please design a mutated version of this algorithm, guided by the "
            "prior reflection, for the following function:\n"
            f"{template_text}\n"
            "Return a concise algorithm name and a one-sentence description."
        )

        return await self._generate(
            prompt,
            generation=generation,
            insight_type=InsightType.MUTATION,
            operator="reevo_mutation",
            parent_ids=[parent.id],
        )
