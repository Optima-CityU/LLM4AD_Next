"""MCTS-AHD samplers.

Migrated from the legacy LLM4AD ``method/mcts_ahd``. MCTS-AHD reuses the
i1/e1/e2/m1/m2 operators and adds ``s1`` — a synthesis operator that creates
a new algorithm inspired by all algorithms along a tree path.

All samplers reuse ``BaseUnifiedSampler._generate_unified`` for the final
thought+code generation; only the prompts differ (they explicitly frame the
objective and, for e1/s1, ask for diversity / synthesis).
"""

from __future__ import annotations

from typing import Any

from llm4ad.planner.base import Algorithm, InsightType
from llm4ad.planner.sampler.base import BaseSampler
from llm4ad.planner.sampler.unified_prompt_utils import (
    _UNIFIED_JSON_SUFFIX,
    format_block_context,
    format_parent_context,
)
from llm4ad.planner.sampler.unified_sampler_base import BaseUnifiedSampler


def _base_header(background: str, block: Any) -> str:
    """Build the shared prompt header (background + block context).

    Args:
        background: Task background description.
        block: The EVOLVE block (or None).

    Returns:
        Header string used by all MCTS-AHD prompts.
    """
    return (
        f"{background}\n\n"
        f"Repository context (code surrounding the EVOLVE block):\n{format_block_context(block)}\n\n"
    )


def build_mcts_i1_prompt(background: str, block: Any) -> str:
    """Build the MCTS-AHD initialization prompt."""
    return (
        f"{_base_header(background, block)}"
        "Propose an initial heuristic for the task above.\n"
        "1. First, describe the design idea and main steps of your algorithm.\n"
        "2. Then, write the complete replacement code for the EVOLVE block. "
        "Your code must define the same function(s) that the code after EVOLVE_END calls.\n\n"
        f"{_UNIFIED_JSON_SUFFIX}"
    )


def build_mcts_e1_prompt(background: str, block: Any, parents: list[Algorithm]) -> str:
    """Build the e1 prompt — a totally different form from the given algorithms."""
    return (
        f"{_base_header(background, block)}"
        f"I have {len(parents)} existing algorithms (shown with code for reference):\n"
        f"{format_parent_context(parents)}\n\n"
        "Please create a new algorithm that has a totally different form from the given "
        "algorithms. Try generating code with different structures, flows, or strategies. "
        "Aim for strong performance (higher score is better).\n"
        "1. First, describe the design idea and main steps of your new algorithm.\n"
        "2. Then, write the complete replacement code for the EVOLVE block.\n\n"
        f"{_UNIFIED_JSON_SUFFIX}"
    )


def build_mcts_e2_prompt(background: str, block: Any, parents: list[Algorithm]) -> str:
    """Build the e2 prompt — similar to the last, inspired by the first."""
    n = len(parents)
    return (
        f"{_base_header(background, block)}"
        f"I have {n} existing algorithms (shown with code for reference):\n"
        f"{format_parent_context(parents)}\n\n"
        f"Please create a new algorithm inspired by the shared ideas of the algorithms above, "
        f"aiming for a score higher than any of them.\n"
        "1. First, list the common ideas that may give good performance.\n"
        "2. Then, describe the design idea and main steps of your new algorithm.\n"
        "3. Finally, write the complete replacement code for the EVOLVE block.\n\n"
        f"{_UNIFIED_JSON_SUFFIX}"
    )


def build_mcts_m1_prompt(background: str, block: Any, parent: Algorithm) -> str:
    """Build the m1 prompt — a structurally modified version with novel mechanisms."""
    return (
        f"{_base_header(background, block)}"
        f"I have one algorithm:\n{format_parent_context([parent])}\n\n"
        "Please create a new algorithm that is a modified version of the provided one, "
        "introducing novel mechanisms, new equations, or new program segments.\n"
        "1. First, describe your new algorithm and main steps.\n"
        "2. Then, write the complete replacement code for the EVOLVE block.\n\n"
        f"{_UNIFIED_JSON_SUFFIX}"
    )


def build_mcts_m2_prompt(background: str, block: Any, parent: Algorithm) -> str:
    """Build the m2 prompt — different parameter settings of the provided algorithm."""
    return (
        f"{_base_header(background, block)}"
        f"I have one algorithm:\n{format_parent_context([parent])}\n\n"
        "Please identify the main parameters and create a new algorithm with different "
        "parameter settings in its equations compared to the provided algorithm.\n"
        "1. First, describe your new algorithm and which parameters you change.\n"
        "2. Then, write the complete replacement code for the EVOLVE block.\n\n"
        f"{_UNIFIED_JSON_SUFFIX}"
    )


def build_mcts_s1_prompt(background: str, block: Any, parents: list[Algorithm]) -> str:
    """Build the s1 synthesis prompt — inspired by all algorithms on the path."""
    return (
        f"{_base_header(background, block)}"
        f"I have {len(parents)} existing algorithms (shown with code for reference):\n"
        f"{format_parent_context(parents)}\n\n"
        "Please create a new algorithm inspired by ALL of the above algorithms, with a "
        "score higher than any of them.\n"
        "1. First, list the ideas from the provided algorithms that are clearly helpful.\n"
        "2. Then, based on those ideas, describe the design idea and main steps of your "
        "new algorithm.\n"
        "3. Finally, write the complete replacement code for the EVOLVE block.\n\n"
        f"{_UNIFIED_JSON_SUFFIX}"
    )


class _BaseMCTSSampler(BaseUnifiedSampler):
    """Shared base for MCTS-AHD samplers (reuses unified generation)."""


@BaseSampler.register("mcts_i1_sampler")
class MCTSI1Sampler(_BaseMCTSSampler):
    """Generate an initial MCTS-AHD algorithm."""

    @property
    def n_parents(self) -> int:
        """No parents required."""
        return 0

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "mcts_i1_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Sample an initial algorithm."""
        prompt = build_mcts_i1_prompt(kwargs.get("background", ""), self._first_block())
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.INITIAL,
            operator="i1",
            parent_ids=[],
        )


@BaseSampler.register("mcts_e1_sampler")
class MCTSE1Sampler(_BaseMCTSSampler):
    """Create an algorithm with a totally different form (root-level cross)."""

    @property
    def n_parents(self) -> int:
        """Uses one representative per root child; at least one parent."""
        return 1

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "mcts_e1_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Sample a divergent algorithm from the provided parents."""
        parents = kwargs.get("parents", population)
        prompt = build_mcts_e1_prompt(kwargs.get("background", ""), self._first_block(), parents)
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.CROSSOVER,
            operator="e1",
            parent_ids=[p.id for p in parents],
        )


@BaseSampler.register("mcts_e2_sampler")
class MCTSE2Sampler(_BaseMCTSSampler):
    """Create an algorithm similar to one parent, inspired by another."""

    @property
    def n_parents(self) -> int:
        """Needs two parents."""
        return 2

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "mcts_e2_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Sample a backbone-inspired algorithm from two parents."""
        parents = kwargs.get("parents", population)
        if len(parents) < 2:
            raise ValueError("MCTS e2 requires two parents")
        prompt = build_mcts_e2_prompt(kwargs.get("background", ""), self._first_block(), parents)
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.CROSSOVER,
            operator="e2",
            parent_ids=[p.id for p in parents],
        )


@BaseSampler.register("mcts_m1_sampler")
class MCTSM1Sampler(_BaseMCTSSampler):
    """Structurally modify one algorithm with novel mechanisms."""

    @property
    def n_parents(self) -> int:
        """Needs one parent."""
        return 1

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "mcts_m1_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Sample a structurally mutated algorithm."""
        parents = kwargs.get("parents", population)
        if not parents:
            raise ValueError("MCTS m1 requires one parent")
        prompt = build_mcts_m1_prompt(kwargs.get("background", ""), self._first_block(), parents[0])
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.MUTATION,
            operator="m1",
            parent_ids=[parents[0].id],
        )


@BaseSampler.register("mcts_m2_sampler")
class MCTSM2Sampler(_BaseMCTSSampler):
    """Re-parameterize one algorithm."""

    @property
    def n_parents(self) -> int:
        """Needs one parent."""
        return 1

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "mcts_m2_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Sample a re-parameterized algorithm."""
        parents = kwargs.get("parents", population)
        if not parents:
            raise ValueError("MCTS m2 requires one parent")
        prompt = build_mcts_m2_prompt(kwargs.get("background", ""), self._first_block(), parents[0])
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.MUTATION,
            operator="m2",
            parent_ids=[parents[0].id],
        )


@BaseSampler.register("mcts_s1_sampler")
class MCTSS1Sampler(_BaseMCTSSampler):
    """Synthesize a new algorithm inspired by all algorithms on a tree path."""

    @property
    def n_parents(self) -> int:
        """Uses a path of algorithms; at least one parent."""
        return 1

    @property
    def name(self) -> str:
        """Return the sampler name."""
        return "mcts_s1_sampler"

    async def sample(self, population: list[Algorithm], generation: int, **kwargs: Any) -> Algorithm:
        """Sample a synthesized algorithm from the provided path."""
        parents = kwargs.get("parents", population)
        if not parents:
            raise ValueError("MCTS s1 requires at least one parent")
        prompt = build_mcts_s1_prompt(kwargs.get("background", ""), self._first_block(), parents)
        return await self._generate_unified(
            prompt,
            generation=generation,
            insight_type=InsightType.CROSSOVER,
            operator="s1",
            parent_ids=[p.id for p in parents],
        )
