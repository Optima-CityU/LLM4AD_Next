"""EoH M1 sampler: create a modified version of one algorithm.

Migrated from legacy LLM4AD EoH method (llm4ad/method/eoh/prompt.py). The M1
operator asks the LLM to create a new algorithm that is a modified version of
a single parent, with a different form.
"""

from __future__ import annotations

from typing import Any

from llm4ad.planner.base import Algorithm, InsightType
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.eoh_i1_sampler import _BaseEoHSampler


@BaseSampler.register("eoh_m1_sampler")
class EoHM1Sampler(_BaseEoHSampler):
    """EoH M1 sampler: structural modification of one parent.

    Creates a new algorithm that is a modified version of the parent with a
    different form.
    """

    @property
    def n_parents(self) -> int:
        """Number of parent algorithms required (1 for M1)."""
        return 1

    @property
    def name(self) -> str:
        """Get the sampler name."""
        return "eoh_m1_sampler"

    async def sample(
        self,
        parents: list[Algorithm],
        generation: int,
        **kwargs: Any,
    ) -> Algorithm:
        """Generate a modified version of the parent algorithm.

        Args:
            parents: List containing exactly 1 parent algorithm.
            generation: Current generation number.
            **kwargs: Additional parameters (background, template_function).

        Returns:
            Algorithm with a modified insight.
        """
        if len(parents) != 1:
            raise ValueError(f"EoH M1 requires exactly 1 parent, got {len(parents)}")

        parent = parents[0]
        background = kwargs.get("background", "")
        template_text = self._template_text(kwargs)

        prompt = (
            f"{background}\n"
            "I have one algorithm with its code as follows. Algorithm description:\n"
            f"{parent.description}\n"
            "Code:\n"
            f"{self._get_parent_code(parent)}\n"
            "Please assist me in creating a new algorithm that has a different "
            "form but can be a modified version of the algorithm provided.\n"
            "1. First, describe your new algorithm and main steps in one sentence.\n"
            "2. Next, design a Python implementation for the following function:\n"
            f"{template_text}\n"
            "Return a concise algorithm name and a one-sentence description."
        )

        return await self._generate(
            prompt,
            generation=generation,
            insight_type=InsightType.MUTATION,
            operator="m1",
            parent_ids=[parent.id],
        )
