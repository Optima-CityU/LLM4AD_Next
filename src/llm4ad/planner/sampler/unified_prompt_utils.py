"""Neutral prompt-formatting utilities shared across evolution methods.

These helpers are method-agnostic: they format EVOLVE block context, parent
algorithms, and the shared JSON output contract used by every "unified"
sampler (thought + code in a single LLM call). Method-specific prompt text
lives in each method's own ``*_prompt_templates`` module and composes these
building blocks.
"""

from __future__ import annotations

import re

from llm4ad.infra.repo_analyzer.base import EvolveBlock
from llm4ad.planner.base import Algorithm


def format_block_context(block: EvolveBlock | None) -> str:
    """Format EVOLVE block context for prompts.

    Args:
        block: The EVOLVE block, or None when no context is available.

    Returns:
        A human-readable block-context string for inclusion in prompts.
    """
    if block is None:
        return "No EVOLVE block context is available."

    return (
        f"Target file: {block.file_path}\n"
        f"Language: {block.language}\n"
        f"Block name: {block.block_name or 'unnamed'}\n"
        "Context before:\n"
        f"{block.context_before}\n\n"
        "Current block content:\n"
        f"{block.original_content}\n\n"
        "Context after:\n"
        f"{block.context_after}\n"
    )


def summarize_algorithm_code(algorithm: Algorithm) -> str:
    """Create a code summary for an algorithm.

    Shows full code content (first few artifacts) so operators can understand
    and diverge from existing implementations.

    Args:
        algorithm: Algorithm whose code artifacts are summarized.

    Returns:
        A concatenated code summary, or a placeholder when no code exists.
    """
    if not algorithm.code_artifacts:
        return "No code artifacts."

    chunks: list[str] = []
    for artifact in algorithm.code_artifacts[:3]:
        chunks.append(f"File: {artifact.file_path}\n{artifact.content.strip()}")
    return "\n\n".join(chunks)


def format_parent_context(parents: list[Algorithm]) -> str:
    """Format parent algorithms for prompts (includes code).

    Args:
        parents: Parent algorithms to describe.

    Returns:
        A formatted parent context string, or a placeholder when empty.
    """
    if not parents:
        return "No parent algorithms are provided."

    sections: list[str] = []
    for index, parent in enumerate(parents, start=1):
        sections.append(
            "\n".join(
                [
                    f"Parent {index}: {parent.name or parent.id}",
                    f"Description: {parent.description}",
                    f"Metrics: {parent.metrics}",
                    f"Score: {parent.score}",
                    f"Code summary: {summarize_algorithm_code(parent)}",
                ]
            )
        )
    return "\n\n".join(sections)


def format_parent_summaries_no_code(parents: list[Algorithm]) -> str:
    """Format parent algorithms without code — only descriptions and metrics.

    Args:
        parents: Parent algorithms to describe.

    Returns:
        A formatted summary string, or a placeholder when empty.
    """
    if not parents:
        return "No parent algorithms are provided."

    sections: list[str] = []
    for index, parent in enumerate(parents, start=1):
        sections.append(
            "\n".join(
                [
                    f"Algorithm {index}: {parent.name or parent.id}",
                    f"Description: {parent.description}",
                    f"Metrics: {parent.metrics}",
                    f"Score: {parent.score}",
                ]
            )
        )
    return "\n\n".join(sections)


def extract_function_skeleton(block: EvolveBlock | None) -> str:
    """Extract function signature from an EVOLVE block with an empty body.

    Returns the function definition line(s) followed by ``pass``, forcing
    the LLM to write the implementation from scratch rather than copying
    from an existing body.

    Args:
        block: The EVOLVE block, or None.

    Returns:
        A function skeleton string.
    """
    if block is None:
        return "# No EVOLVE block available — write a standalone function."

    content = block.original_content.strip()
    lines = content.split("\n")

    header_lines: list[str] = []
    for line in lines:
        header_lines.append(line)
        stripped = line.rstrip()
        if stripped.endswith(":") and not stripped.startswith("#"):
            break
        if stripped.endswith("):"):
            break

    if not header_lines:
        return content

    indent = ""
    match = re.match(r"(\s+)", lines[0])
    if match:
        indent = match.group(1)

    body_indent = indent + "    "
    return "\n".join(header_lines) + f"\n{body_indent}pass  # Your implementation here"


# Shared JSON format instruction for all unified prompts (thought + code).
_UNIFIED_JSON_SUFFIX = (
    'You MUST respond with a valid JSON object containing exactly three fields:\n'
    '{"name": "<algorithm name>", "description": "<your algorithm description>", '
    '"code": "<complete replacement code>"}\n'
    "CRITICAL JSON rules:\n"
    "- The entire response must be a single JSON object on one line or properly escaped\n"
    "- In the 'code' field, ALL newlines must be escaped as \\n (backslash-n), NOT literal newlines\n"
    "- In the 'code' field, ALL double-quotes must be escaped as \\\" \n"
    "The 'code' field must contain the COMPLETE code to place between "
    "EVOLVE_START and EVOLVE_END markers. This MUST include:\n"
    "- import statements at the top (e.g. import sys, import json, import numpy as np)\n"
    "- The function definition with the EXACT SAME NAME AND SIGNATURE as the function "
    "that already exists in 'Current block content' — do NOT rename it\n"
    "The code after EVOLVE_END calls the function by name and depends on these imports, "
    "so keep the exact function name from 'Current block content'. "
    "Do not include markdown fences or the EVOLVE markers themselves in the code field.\n"
    "IMPORTANT: The function receives 'nodes' as a Python list of (x, y) tuples (from json.loads), "
    "NOT a numpy array. You must convert it with np.array(nodes) before doing any numpy operations."
)
