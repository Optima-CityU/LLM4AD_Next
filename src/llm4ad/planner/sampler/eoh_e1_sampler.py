"""EoH E1 sampler: create a new algorithm distinct from parents.

Migrated from legacy LLM4AD EoH method (llm4ad/method/eoh/prompt.py). The E1
operator asks the LLM to design a new algorithm with a totally different form
from the given parent algorithms.
"""

from __future__ import annotations

from typing import Any

from llm4ad.planner.base import Algorithm, InsightType
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.eoh_i1_sampler import _BaseEoHSampler


@BaseSampler.register("eoh_e1_sampler")
class EoHE1Sampler(_BaseEoHSampler):
    """EoH E1 sampler: totally different form from the parents.

    Uses one or more parents as reference and asks the LLM to create a new
    algorithm with a fundamentally different structure.
    """

    def __init__(self, provider, memory, config, analyzed_repository=None) -> None:
        """Initialize the E1 sampler."""
        super().__init__(provider, memory, config, analyzed_repository)
        self._parent_count = max(1, int(self._get_config_value("selection_num", config, default=2)))

    @property
    def n_parents(self) -> int:
        """Number of parent algorithms required (>=1, default 2 for E1)."""
        return self._parent_count

    @property
    def name(self) -> str:
        """Get the sampler name."""
        return "eoh_e1_sampler"

    async def sample(
        self,
        parents: list[Algorithm],
        generation: int,
        **kwargs: Any,
    ) -> Algorithm:
        """Generate a new algorithm with a totally different form.

        Args:
            parents: One or more parent algorithms.
            generation: Current generation number.
            **kwargs: Additional parameters (background, template_function).

        Returns:
            Algorithm with a distinct new insight.
        """
        if not parents:
            raise ValueError("EoH E1 requires at least one parent")

        background = kwargs.get("background", "")
        template_text = self._template_text(kwargs)

        indivs_prompt = ""
        for i, parent in enumerate(parents):
            indivs_prompt += (
                f"No. {i + 1} algorithm and the corresponding code are:\n"
                f"{parent.description}\n{self._get_parent_code(parent)}\n"
            )

        prompt = (
            f"{background}\n"
            f"I have {len(parents)} existing algorithms with their codes as follows:\n"
            f"{indivs_prompt}\n"
            "Please help me create a new algorithm that has a totally different "
            "form from the given ones.\n"
            "1. First, describe your new algorithm and main steps in one sentence.\n"
            "2. Next, design a Python implementation for the following function:\n"
            f"{template_text}\n"
            "Return a concise algorithm name and a one-sentence description."
        )

        return await self._generate(
            prompt,
            generation=generation,
            insight_type=InsightType.CROSSOVER,
            operator="e1",
            parent_ids=[parent.id for parent in parents],
        )
