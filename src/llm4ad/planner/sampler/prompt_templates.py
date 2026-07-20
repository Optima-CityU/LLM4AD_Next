"""Prompt templates for initial algorithm generation.

This module stores all prompt templates used by the samplers for
generating initial algorithm designs and insights.  It also provides
multimodal prompt builder functions that construct structured content
parts (text + images) for vision-capable LLMs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm4ad.infra.provider.base import ContentPart
    from llm4ad.infra.repo_analyzer.base import EvolveBlock
    from llm4ad.planner.base import Algorithm

# Default template for initial algorithm generation when evolving a specific EVOLVE block
INITIAL_ALGORITHM_FROM_BLOCK = """\
You are an expert algorithm designer working on designing a high-performance algorithm.

# Problem Background

{background}

# Accumulated Knowledge

{memory_context}

# Context: Existing Codebase

We are working on an existing codebase and need to evolve/improve the algorithm.
The codebase already has a framework set up, and we need to evolve the specific region marked below.

# Evolvable Region Information

File: {file_path}
Language: {language}
Block Name: {block_name}

## Context Before the Block:
```{language}
{context_before}
```

## Current Implementation:
```{language}
{current_content}
```

## Context After the Block:
```{language}
{context_after}
```

# Your Task

Please design an improved implementation for this evolvable region.
Your goal is to create a better algorithm that achieves better performance
according to the problem background.

Provide your response as a natural language description of the improved algorithm, including:
1. The key ideas of your new approach
2. Why you expect this to perform better
3. The overall algorithm structure/strategy

Be specific about the algorithmic changes you propose.
"""

# Default template for mutating an existing algorithm
MUTATE_ALGORITHM_FROM_BLOCK = """\
You are an expert algorithm designer working to improve an existing algorithm through mutation.

# Problem Background

{background}

# Accumulated Knowledge

{memory_context}

# Current Algorithm Information

We have an existing algorithm in our population that we want to mutate.
Current algorithm score: {score:.4f}
Current algorithm description: {description}

# Evolvable Region to Mutate

File: {file_path}
Language: {language}
Block Name: {block_name}

## Context Before the Block:
```{language}
{context_before}
```

## Current Implementation:
```{language}
{current_content}
```

## Context After the Block:
```{language}
{context_after}
```

# Your Task

Please propose a mutation (small change) to improve this algorithm.
Focus on making an incremental improvement based on the existing implementation.

Provide your response as a natural language description of the mutation, including:
1. The specific change you propose
2. Why you expect this change to improve performance
3. How your change integrates with the existing structure

Be specific about the algorithmic changes you propose.
"""

# Template for crossover combining two parent algorithms
CROSSOVER_ALGORITHM = """\
You are an expert algorithm designer combining two good algorithm designs into an improved child.

# Problem Background

{background}

# Accumulated Knowledge

{memory_context}

# Parent Algorithm 1

Score: {score1:.4f}
Description: {description1}

# Parent Algorithm 2

Score: {score2:.4f}
Description: {description2}

# Your Task

Combine the best ideas from both parent algorithms to create a new child
algorithm that should have better performance than either parent.

Provide your response as a natural language description of the combined algorithm, including:
1. Which key ideas you take from each parent
2. How you combine them into a coherent new algorithm
3. Why you expect this combination to perform better than either parent

Be specific about the algorithmic structure.
"""

# Template for extracting/generating a concise algorithm name from the full description
SUMMARIZE_ALGORITHM_NAME = """\
Below is a detailed description of an algorithm you've designed.
Please provide a concise, descriptive name for this algorithm that captures
the main idea or approach.

# Algorithm Description:
{description}

# Instructions:
- Provide just the algorithm name, no additional explanation or text
- Keep the name between 3-10 words
- Use title case (each major word capitalized)
- Capture the key algorithmic approach or innovation in the name

Example good output:
Hybrid Quick Sort with Insertion Fallback

Example bad output (too long):
This is a hybrid sorting algorithm that combines quick sort with insertion sort for small subarrays which gives better performance than bubble sort

Algorithm Name:
"""

# Template for implementing algorithm description into executable code
IMPLEMENT_ALGORITHM = """\
You are an expert software engineer implementing an algorithm based on a high-level description.

# Problem Background

{background}

# Algorithm Description

{description}

# Implementation Instructions

The main implementation should be placed between `EVOLVE_START` and `EVOLVE_END` markers in the target file.
You can also modify other files in the project if needed for the implementation.

Please:
1. Implement the algorithm described above
2. Include proper comments
3. Ensure the code is fully functional and runnable
4. If this is a modification of an existing algorithm (mutation or crossover), only change what needs to be changed
5. Write clean, efficient, and well-documented code

Now implement the code and write the files.
"""

# Template for implementing algorithm description into code using a plain LLM (no agent/tool-use).
# The LLM must return all code in markdown code blocks with file path annotations.
IMPLEMENT_ALGORITHM_PLAIN_LLM = """\
You are an expert software engineer implementing an algorithm based on a high-level description.

# Problem Background

{background}

# Algorithm Description

{description}

{project_context}

# Implementation Instructions

Implement the algorithm described above. The main implementation should be placed between \
`EVOLVE START` and `EVOLVE END` markers.

Requirements:
1. Implement the algorithm as described
2. Include proper comments
3. Ensure the code is fully functional and runnable
4. Write clean, efficient, and well-documented code
5. You MUST implement the algorithm exactly as described above - do NOT reuse or copy any placeholder code
6. The EVOLVE block currently contains a placeholder; replace it entirely with your complete, novel implementation

# Output Format

Return ALL code files using fenced code blocks with a file path annotation.
Use this exact format for EACH file:

```<language>:<relative/path/to/file>
<code content>
```

For example:

```python:src/algorithm.py
def solve():
    pass
```

```cpp:src/solver.cpp
#include <iostream>
int main() {{ return 0; }}
```

IMPORTANT:
- Every code block MUST have `:filepath` after the language identifier
- Use relative paths from the project root
- If modifying an existing file, output the COMPLETE file content (not just the changed parts)
"""

# Template for mutation using a plain LLM (no agent/tool-use)
MUTATE_ALGORITHM_PLAIN_LLM = """\
You are an expert algorithm designer. Modify the following code block according to the given insight.

# Problem Background

{background}

# Mutation Insight

{insight}

# Current Code

File: {file_path}

```{language}
{original_block}
```

# Task

Apply a small, targeted mutation to improve this code block based on the insight above.

{project_context}

# Output Format

Return the modified code in a fenced code block with file path annotation:

```{language}:{file_path}
<your modified code here>
```

IMPORTANT:
- Do NOT include the EVOLVE START/EVOLVE END comment markers in your output
- Only output the code that goes BETWEEN the markers
- Return the COMPLETE modified block, not just the changed lines
"""

# Template for extracting a positive insight from a well-performing algorithm
EXTRACT_GOOD_MEMORY = """\
You are an expert algorithm analyst. This algorithm performed WELL. \
Extract a concise, reusable insight about what made it successful.

# Problem Background

{background}

# Algorithm

Name: {algorithm_name}
Score: {score}
Generation: {generation}
Description:
{description}

# Evaluation Result

{evaluation_summary}

# Task

Extract one actionable insight (2-5 sentences) about:
1. What key design decisions led to this good performance
2. What patterns or strategies are worth reusing in future designs
"""

# Template for extracting an avoidance lesson from a poorly-performing algorithm
EXTRACT_BAD_MEMORY = """\
You are an expert algorithm analyst. This algorithm performed POORLY or failed. \
Extract a concise lesson about what went wrong and what to avoid.

# Problem Background

{background}

# Algorithm

Name: {algorithm_name}
Score: {score}
Generation: {generation}
Description:
{description}

# Evaluation Result / Error

{evaluation_summary}

# Task

Extract one actionable lesson (2-5 sentences) about:
1. What design decisions or patterns led to poor performance or failure
2. What specific pitfalls future algorithm designs should AVOID
"""

# Template for extracting a repair lesson when evaluation did not produce a score
EXTRACT_EXECUTION_FAILURE_MEMORY = """\
You are an expert algorithm analyst. Evaluation execution failed before a valid
score was produced. Do not infer that the algorithm scored zero or performed
poorly. Extract a concise, reusable lesson about preventing or repairing the
concrete implementation or constraint failure shown by the error evidence.

# Problem Background

{background}

# Algorithm

Name: {algorithm_name}
Score: {score}
Generation: {generation}
Description:
{description}

# Evaluation Error

{evaluation_summary}

# Task

Extract one actionable lesson (2-5 sentences) about:
1. The concrete implementation or constraint violation that caused execution to fail
2. The validation, guard, or design change that future algorithms should apply to avoid it
"""

# Template for extracting a positive insight from a well-performing algorithm
EXTRACT_GOOD_MEMORY = """\
You are an expert algorithm analyst. This algorithm performed WELL. \
Extract a concise, reusable insight about what made it successful.

# Problem Background

{background}

# Algorithm

Name: {algorithm_name}
Score: {score}
Generation: {generation}
Description:
{description}

# Evaluation Result

{evaluation_summary}

# Task

Extract one actionable insight (2-5 sentences) about:
1. What key design decisions led to this good performance
2. What patterns or strategies are worth reusing in future designs
"""

# Template for extracting an avoidance lesson from a poorly-performing algorithm
EXTRACT_BAD_MEMORY = """\
You are an expert algorithm analyst. This algorithm performed POORLY or failed. \
Extract a concise lesson about what went wrong and what to avoid.

# Problem Background

{background}

# Algorithm

Name: {algorithm_name}
Score: {score}
Generation: {generation}
Description:
{description}

# Evaluation Result / Error

{evaluation_summary}

# Task

Extract one actionable lesson (2-5 sentences) about:
1. What design decisions or patterns led to poor performance or failure
2. What specific pitfalls future algorithm designs should AVOID
"""


# ---------------------------------------------------------------------------
# Multimodal prompt builder functions
# ---------------------------------------------------------------------------

def _make_text_part(text: str) -> ContentPart:
    """Create a text ContentPart."""
    from llm4ad.infra.provider.base import ContentPart
    return ContentPart(type="text", text=text)


def _make_image_part(base64_data: str, media_type: str = "image/png") -> ContentPart:
    """Create an image_url ContentPart from base64 data."""
    from llm4ad.infra.provider.base import ContentPart
    data_url = f"data:{media_type};base64,{base64_data}"
    return ContentPart(type="image_url", image_url={"url": data_url})


def _collect_behavior_parts(
    algorithm: Algorithm,
    label: str,
    multimodal_config: dict[str, Any],
) -> list[ContentPart]:
    """Collect behavior observation text and images from an algorithm.

    Args:
        algorithm: Algorithm with potential behavior data.
        label: Human-readable label for this algorithm in the prompt.
        multimodal_config: Multimodal configuration dict with keys
            ``include_observation_text``, ``max_images_per_prompt``,
            ``image_max_size_kb``.

    Returns:
        List of ContentPart (text observations + images).
    """
    parts: list[ContentPart] = []

    if multimodal_config.get("include_observation_text", True):
        obs = algorithm.get_observation_text()
        if obs:
            parts.append(_make_text_part(f"{label} behavior observation:\n{obs}"))

    max_images = multimodal_config.get("max_images_per_prompt", 3)
    max_kb = multimodal_config.get("image_max_size_kb", 512)

    from llm4ad.evaluator.renderer import render_visualization

    image_count = 0
    for bd in algorithm.behavior_data:
        for viz in bd.visualizations:
            if image_count >= max_images:
                break
            img_data = render_visualization(viz)
            if img_data is not None:
                # Compress if needed
                from llm4ad.utils.image_utils import compress_image_base64
                img_data = compress_image_base64(img_data, max_kb=max_kb)
                parts.append(_make_text_part(f"{label} behavior visualization ({viz.label}):"))
                parts.append(_make_image_part(img_data, viz.media_type))
                image_count += 1
    return parts


def build_multimodal_mutation_prompt(
    background: str,
    parent: Algorithm,
    block: EvolveBlock | None,
    multimodal_config: dict[str, Any],
) -> list[ContentPart]:
    """Build a mutation prompt with parent's behavior visualization.

    Constructs structured multimodal content: the existing mutation
    prompt text followed by behavior observations and images from the
    parent algorithm.

    Args:
        background: Problem background text.
        parent: Parent algorithm to mutate (with behavior data).
        block: Evolvable code block (optional).
        multimodal_config: Multimodal configuration dict.

    Returns:
        List of ContentPart for a multimodal ChatMessage.
    """
    parent_score = parent.evaluation.score if parent.evaluation else 0.0

    if block:
        text_prompt = MUTATE_ALGORITHM_FROM_BLOCK.format(
            background=background,
            file_path=block.file_path,
            language=block.language,
            block_name=block.block_name,
            context_before=block.context_before,
            current_content=block.original_content,
            context_after=block.context_after,
            score=parent_score,
            description=parent.description,
        )
    else:
        text_prompt = (
            f"You are an expert algorithm designer working to improve an existing "
            f"algorithm through mutation.\n\n"
            f"# Problem Background\n\n{background}\n\n"
            f"# Current Algorithm Information\n\n"
            f"Current algorithm score: {parent_score:.4f}\n"
            f"Current algorithm description: {parent.description}\n"
        )

    parts: list[ContentPart] = [_make_text_part(text_prompt)]

    # Add behavior data
    parts.extend(_collect_behavior_parts(parent, "Parent algorithm", multimodal_config))

    # Add multimodal-specific instruction
    parts.append(_make_text_part(
        "\n# Behavior Analysis Instructions\n\n"
        "Study the behavior visualization above carefully. Identify specific "
        "weaknesses or inefficiencies visible in the algorithm's behavior, "
        "then propose a targeted mutation that addresses these issues.\n\n"
        "Provide your response as a natural language description of the mutation, including:\n"
        "1. What you observed in the behavior visualization\n"
        "2. The specific change you propose based on this observation\n"
        "3. Why you expect this change to improve performance\n"
    ))

    return parts


def build_multimodal_crossover_prompt(
    background: str,
    parent1: Algorithm,
    parent2: Algorithm,
    multimodal_config: dict[str, Any],
) -> list[ContentPart]:
    """Build a crossover prompt with both parents' behavior visualizations.

    Args:
        background: Problem background text.
        parent1: First parent algorithm.
        parent2: Second parent algorithm.
        multimodal_config: Multimodal configuration dict.

    Returns:
        List of ContentPart for a multimodal ChatMessage.
    """
    score1 = parent1.evaluation.score if parent1.evaluation else 0.0
    score2 = parent2.evaluation.score if parent2.evaluation else 0.0

    text_prompt = CROSSOVER_ALGORITHM.format(
        background=background,
        memory_context="",
        score1=score1,
        description1=parent1.description,
        score2=score2,
        description2=parent2.description,
    )

    parts: list[ContentPart] = [_make_text_part(text_prompt)]

    # Add behavior data from both parents
    parts.extend(_collect_behavior_parts(parent1, "Parent 1", multimodal_config))
    parts.extend(_collect_behavior_parts(parent2, "Parent 2", multimodal_config))

    # Add multimodal-specific instruction
    parts.append(_make_text_part(
        "\n# Behavior Comparison Instructions\n\n"
        "Compare the behavior visualizations of both parent algorithms above. "
        "Identify the strengths and weaknesses visible in each algorithm's behavior, "
        "then combine the best aspects into a new child algorithm.\n\n"
        "Provide your response as a natural language description of the combined algorithm, including:\n"
        "1. What you observed in each parent's behavior\n"
        "2. Which key ideas you take from each parent\n"
        "3. How you combine them into a coherent new algorithm\n"
    ))

    return parts
