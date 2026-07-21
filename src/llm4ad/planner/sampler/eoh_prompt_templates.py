"""Prompt templates for the EoH planner samplers.

EoH owns its own copy of the I1/E1/E2/M1/M2 unified (thought + code) prompts.
The text starts out equivalent to MEoH's but is independent so the two
methods can evolve separately. Neutral formatting helpers are imported from
``unified_prompt_utils``.

Reference:
    Fei Liu et al. "Evolution of Heuristics: Towards Efficient Automatic
    Algorithm Design Using Large Language Model." ICML 2024.
"""

from __future__ import annotations

from llm4ad.infra.repo_analyzer.base import EvolveBlock
from llm4ad.planner.base import Algorithm
from llm4ad.planner.sampler.unified_prompt_utils import (
    _UNIFIED_JSON_SUFFIX,
    format_block_context,
    format_parent_context,
)


def build_i1_unified_prompt(background: str, block: EvolveBlock | None) -> str:
    """Build the unified I1 prompt (initial algorithm with code)."""
    block_ctx = format_block_context(block)
    return (
        f"{background}\n\n"
        f"Repository context (code surrounding the EVOLVE block):\n{block_ctx}\n\n"
        "Please propose an initial algorithm for the task described above.\n"
        "1. First, describe your algorithm: name it and explain the main idea.\n"
        "2. Then, write the complete replacement code for the EVOLVE block. "
        "Your code must define the same function(s) that the code after EVOLVE_END calls. "
        "Look at the 'Context after' section to see what function name and signature is expected.\n\n"
        f"{_UNIFIED_JSON_SUFFIX}"
    )


def build_e1_unified_prompt(
    background: str, block: EvolveBlock | None, parents: list[Algorithm]
) -> str:
    """Build the unified E1 prompt — totally different form, with parent code as reference."""
    parent_context = format_parent_context(parents)
    block_ctx = format_block_context(block)
    return (
        f"{background}\n\n"
        f"Repository context (code surrounding the EVOLVE block):\n{block_ctx}\n\n"
        f"I have {len(parents)} existing algorithms (shown with code for reference):\n"
        f"{parent_context}\n\n"
        "Please help me create a new algorithm that has a totally different form "
        "from the given ones.\n"
        "IMPORTANT: The parent code is provided only as REFERENCE to understand what "
        "has been tried. Do NOT copy or closely imitate their structure — your algorithm "
        "should use a fundamentally different approach or strategy.\n"
        "1. First, describe your new algorithm and main steps.\n"
        "2. Then, write the complete replacement code for the EVOLVE block. "
        "Your code must define the same function(s) that the code after EVOLVE_END calls. "
        "Look at the 'Context after' section to see what function name and signature is expected.\n\n"
        f"{_UNIFIED_JSON_SUFFIX}"
    )


def build_e2_unified_prompt(
    background: str, block: EvolveBlock | None, parents: list[Algorithm]
) -> str:
    """Build the unified E2 prompt — backbone-inspired, with parent code as reference."""
    parent_context = format_parent_context(parents)
    block_ctx = format_block_context(block)
    return (
        f"{background}\n\n"
        f"Repository context (code surrounding the EVOLVE block):\n{block_ctx}\n\n"
        f"I have {len(parents)} existing algorithms (shown with code for reference):\n"
        f"{parent_context}\n\n"
        "Please help me create a new algorithm that has a different form but is "
        "motivated by the backbone idea of the given algorithms.\n"
        "IMPORTANT: The parent code is provided only as REFERENCE. Identify the shared "
        "backbone IDEA (not code), then build a new algorithm that differs in implementation.\n"
        "1. First, identify the shared backbone idea from the algorithms above.\n"
        "2. Then, describe a new algorithm that builds on this backbone but differs in form.\n"
        "3. Write the complete replacement code for the EVOLVE block. "
        "Your code must define the same function(s) that the code after EVOLVE_END calls. "
        "Look at the 'Context after' section to see what function name and signature is expected.\n\n"
        f"{_UNIFIED_JSON_SUFFIX}"
    )


def build_m1_unified_prompt(
    background: str, block: EvolveBlock | None, parent: Algorithm
) -> str:
    """Build the unified M1 prompt — structural mutation, includes parent code."""
    parent_context = format_parent_context([parent])
    block_ctx = format_block_context(block)
    return (
        f"{background}\n\n"
        f"Repository context (code surrounding the EVOLVE block):\n{block_ctx}\n\n"
        f"I have one algorithm:\n{parent_context}\n\n"
        "Please help me create a modified version of this algorithm with a noticeably "
        "different structure or search strategy, while keeping it relevant to the task.\n"
        "The parent code is provided as reference. You should make structural changes to "
        "the approach, not just surface-level modifications.\n"
        "1. First, describe your new algorithm and what structural changes you make.\n"
        "2. Then, write the complete replacement code for the EVOLVE block. "
        "Your code must define the same function(s) that the code after EVOLVE_END calls. "
        "Look at the 'Context after' section to see what function name and signature is expected.\n\n"
        f"{_UNIFIED_JSON_SUFFIX}"
    )


def build_m2_unified_prompt(
    background: str, block: EvolveBlock | None, parent: Algorithm
) -> str:
    """Build the unified M2 prompt — parameter mutation, includes parent code."""
    parent_context = format_parent_context([parent])
    block_ctx = format_block_context(block)
    return (
        f"{background}\n\n"
        f"Repository context (code surrounding the EVOLVE block):\n{block_ctx}\n\n"
        f"I have one algorithm:\n{parent_context}\n\n"
        "Please help me create a modified version of this algorithm that keeps the "
        "high-level idea but changes important parameters, scoring functions, or local rules.\n"
        "The parent code is provided as reference. Focus on tuning parameters, thresholds, "
        "scoring functions, or local heuristic rules.\n"
        "1. First, describe what parameter/rule changes you make and why.\n"
        "2. Then, write the complete replacement code for the EVOLVE block. "
        "Your code must define the same function(s) that the code after EVOLVE_END calls. "
        "Look at the 'Context after' section to see what function name and signature is expected.\n\n"
        f"{_UNIFIED_JSON_SUFFIX}"
    )
