"""Tests for domain-neutral unified evolution prompts."""

from pathlib import Path

from llm4ad.infra.repo_analyzer.base import EvolveBlock
from llm4ad.planner.sampler.meoh_prompt_templates import build_e1_unified_prompt


def test_meoh_unified_prompt_does_not_inject_tsp_assumptions() -> None:
    """A subset-selection task must not receive TSP-specific input instructions."""
    block = EvolveBlock(
        file_path="solve.py",
        absolute_path=Path("solve.py"),
        line_start=5,
        line_end=8,
        comment_style="#",
        block_name="select_items",
        original_content=(
            "def select_items(target: int, items: list[int]) -> list[int]:\n"
            "    return []"
        ),
        context_before='"""Subset-selection solver."""',
        context_after="",
        language="python",
    )

    prompt = build_e1_unified_prompt("Optimize subset selection.", block, [])

    assert "select_items(target: int, items: list[int])" in prompt
    assert "unrelated problem domains" in prompt
    assert "function receives 'nodes'" not in prompt
    assert "np.array(nodes)" not in prompt
