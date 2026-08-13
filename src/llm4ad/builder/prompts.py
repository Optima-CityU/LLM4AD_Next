"""LLM prompt templates for the automated task builder.

Centralizes all prompt templates used by TaskAnalyzer, TaskCreator,
and TaskValidator. Templates are loaded with few-shot examples from
the existing application templates at runtime.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Template loading helpers
# ---------------------------------------------------------------------------

_EXAMPLES_DIR: Path | None = None


def _get_examples_dir() -> Path:
    """Locate the examples/applications/task_template_python directory."""
    global _EXAMPLES_DIR
    if _EXAMPLES_DIR is not None:
        return _EXAMPLES_DIR

    # Walk up from this file to find the repo root
    current = Path(__file__).resolve().parent
    for _ in range(10):
        candidate = current / "examples" / "applications" / "task_template_python"
        if candidate.exists():
            _EXAMPLES_DIR = candidate
            return _EXAMPLES_DIR
        current = current.parent

    raise FileNotFoundError(
        "Cannot locate examples/applications/task_template_python/. "
        "Ensure the LLM4AD repository structure is intact."
    )


def _load_example(relative_path: str) -> str:
    """Load an example file from the template directory."""
    path = _get_examples_dir() / relative_path
    if not path.exists():
        return f"[Example file not found: {relative_path}]"
    return path.read_text(encoding="utf-8")


_MULTIMODAL_EXAMPLES_DIR: Path | None = None


def _get_multimodal_examples_dir() -> Path:
    """Locate the examples/applications/task_template_python_multimodal directory."""
    global _MULTIMODAL_EXAMPLES_DIR
    if _MULTIMODAL_EXAMPLES_DIR is not None:
        return _MULTIMODAL_EXAMPLES_DIR

    current = Path(__file__).resolve().parent
    for _ in range(10):
        candidate = current / "examples" / "applications" / "task_template_python_multimodal"
        if candidate.exists():
            _MULTIMODAL_EXAMPLES_DIR = candidate
            return _MULTIMODAL_EXAMPLES_DIR
        current = current.parent

    raise FileNotFoundError(
        "Cannot locate examples/applications/task_template_python_multimodal/. "
        "Ensure the LLM4AD repository structure is intact."
    )


def _load_multimodal_example(relative_path: str) -> str:
    """Load an example file from the multimodal template directory."""
    path = _get_multimodal_examples_dir() / relative_path
    if not path.exists():
        return f"[Multimodal example file not found: {relative_path}]"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Analysis prompts
# ---------------------------------------------------------------------------

ANALYZE_DESCRIPTION_PROMPT = """\
You are an expert algorithm design consultant for the LLM4AD platform.
Analyze the following problem description and produce a structured specification
for building an automated algorithm evolution task.

## Problem Description
{description}

## Your Task
Analyze this problem and output a JSON object with the following fields:

```json
{{
    "problem_type": "<category: combinatorial_optimization | sorting | scheduling | ml | rl | regression | simulation | other>",
    "complexity_tier": "<simple | medium | complex — estimated difficulty of the optimization problem>",
    "project_name": "<short_snake_case_slug for the project, e.g. graph_coloring>",
    "background": "<2-4 sentence background description for the LLM, explaining the optimization goal and constraints>",
    "function_name": "<snake_case function name to evolve, e.g. greedy_coloring>",
    "function_signature": "<full Python def line, e.g. def greedy_coloring(graph: dict) -> dict:>",
    "function_description": "<what the function does, its inputs, and expected outputs>",
    "metrics": [
        {{
            "name": "<metric_name_snake_case>",
            "type": "<maximize | minimize>",
            "weight": <float>,
            "description": "<what this metric measures>"
        }}
    ],
    "input_format": "<description of the input data format the function receives>",
    "output_format": "<description of the expected return value format>",
    "algorithm_dir_name": "<directory name for the algorithm code, e.g. graph_coloring_algorithm>",
    "algorithm_file_name": "<python filename, e.g. coloring.py>",
    "needs_dataset": <true | false>,
    "dataset_description": "<if needs_dataset, describe what kind of data files are needed>"
}}
```

## Guidelines
- The function should be self-contained and take simple data structures as input
- Choose metrics that are directly computable from the algorithm's output
- The first metric should be the primary optimization objective
- Use subprocess pattern: the algorithm file will be executed as a separate process
- Keep the function signature simple — avoid complex custom types
- **complexity_tier**: "simple" for straightforward problems, "medium" for moderate, "complex" for large search spaces or NP-hard
- Output ONLY the JSON object, no additional text
"""

ANALYZE_CODE_PROMPT = """\
You are an expert algorithm design consultant for the LLM4AD platform.
Analyze the following existing codebase and problem description to identify
the best function to evolve and how to evaluate it.

## Problem Description
{description}

## Existing Code Files
{code_summary}

## Dataset Information
{dataset_summary}

## Your Task
Analyze the code and produce a JSON specification for building an LLM4AD task.
Identify which function is the best candidate for evolutionary optimization.

Output a JSON object with the same structure as described below:

```json
{{
    "problem_type": "<category>",
    "complexity_tier": "<simple | medium | complex — estimated difficulty>",
    "project_name": "<short_snake_case_slug>",
    "background": "<2-4 sentence background description>",
    "function_name": "<existing or proposed function name to evolve>",
    "function_signature": "<full Python def line>",
    "function_description": "<what the function does>",
    "metrics": [
        {{"name": "<metric_name>", "type": "<maximize | minimize>", "weight": <float>, "description": "<description>"}}
    ],
    "input_format": "<input data format>",
    "output_format": "<expected output format>",
    "algorithm_dir_name": "<directory name>",
    "algorithm_file_name": "<python filename>",
    "needs_dataset": <true | false>,
    "dataset_description": "<dataset description if needed>",
    "existing_code_analysis": "<summary of how the existing code relates to the task>"
}}
```

Output ONLY the JSON object, no additional text.
"""

ANALYZE_CODE_WITH_EVOLVE_PROMPT = """\
You are an expert algorithm design consultant for the LLM4AD platform.
The user has provided an existing algorithm codebase that already contains
EVOLVE_START / EVOLVE_END markers. The function to evolve has been identified
from the code. Your job is to analyze the code and the user's description to
determine the problem type, metrics, and data formats.

## User's Evolution Goal
{description}

## Full Algorithm File
```python
{algorithm_code}
```

## EVOLVE Block Content (the evolvable region)
```python
{evolve_block_content}
```

## Identified Function
- Name: {function_name}
- Signature: {function_signature}

## Dataset Information
{dataset_summary}

## Your Task
Analyze the code and description, then output a JSON object. You do NOT need
to propose a function name or signature — those are already determined from
the EVOLVE markers. Focus on understanding what the function does and what
metrics make sense for evolving it.

```json
{{{{
    "problem_type": "<category: combinatorial_optimization | sorting | scheduling | ml | rl | regression | simulation | other>",
    "complexity_tier": "<simple | medium | complex — estimated difficulty of the optimization problem>",
    "project_name": "<short_snake_case_slug>",
    "background": "<2-4 sentence background for the LLM, explaining the optimization goal>",
    "function_name": "{function_name}",
    "function_signature": "{function_signature}",
    "function_description": "<what the function does, its inputs, and expected outputs>",
    "metrics": [
        {{{{"name": "<metric_name>", "type": "<maximize | minimize>", "weight": <float>, "description": "<description>"}}}}
    ],
    "input_format": "<description of the input data format the algorithm receives via sys.argv>",
    "output_format": "<description of the JSON output the algorithm prints to stdout>",
    "algorithm_dir_name": "{algorithm_dir_name}",
    "algorithm_file_name": "{algorithm_file_name}"
}}}}
```

Output ONLY the JSON object, no additional text.
"""

CLASSIFY_EVOLVE_ROLE_PROMPT = """\
You are an expert algorithm design consultant analyzing an EVOLVE block's semantic role.

## User's Goal
{description}

## Full Original File (context)
```python
{full_code}
```

## EVOLVE Block (the function to evolve)
```python
{evolve_block}
```

## Function Signature
{function_signature}

## Your Task
Classify the EVOLVE function's role and determine how to wire it into a runnable algorithm.

Output a JSON object:
```json
{{
    "function_role": "<complete_solver | sub_function | helper>",
    "input_schema": {{"<key>": "<type description>"}},
    "output_schema": {{"<key>": "<type description>"}},
    "needed_helpers": ["<function_or_class_name>"],
    "driver_strategy": "<1-2 sentence description of how to wire the EVOLVE function>",
    "feasibility_warning": "<optional: if evolving this function cannot achieve the user's goal, explain why>"
}}
```

**function_role**:
- `complete_solver`: function takes a problem instance and returns a complete solution (e.g. TSP solver)
- `sub_function`: function is a sub-heuristic called inside a driver loop (e.g. CVRP select_next_node)
- `helper`: utility function used by other code

**input_schema**: keys and types of the JSON input the final algorithm file will read from `sys.argv[1]`

**output_schema**: keys and types of the JSON output the algorithm file will print

**needed_helpers**: functions/classes from the original file that the EVOLVE function or driver depends on

**driver_strategy**: how to connect input_schema → EVOLVE function → output_schema

**feasibility_warning**: if evolving this function won't achieve the user's stated goal, explain why

Output ONLY the JSON object, no additional text.
"""

GENERATE_DRIVER_PROMPT = """\
You are an expert Python developer generating a standalone algorithm driver script.

## User's Goal
{description}

## Full Original File (reference)
```python
{full_code}
```

## EVOLVE Block (preserve verbatim)
```python
{evolve_block}
```

## Classification Result
```json
{classifier_output}
```

## Your Task
Generate a single standalone Python file that:
1. Imports standard libraries (json, sys, numpy, etc.)
2. Inlines the helper functions/classes from `needed_helpers` (cleaned of old-API imports)
3. Includes the EVOLVE function between `# EVOLVE_START` and `# EVOLVE_END` markers
4. Implements `solve(input_data: dict) -> dict` that:
   - Takes input matching `input_schema`
   - Runs the driver logic described in `driver_strategy`
   - Returns output matching `output_schema`
5. Implements `main()` that reads JSON from `sys.argv[1]`, calls `solve()`, prints JSON result

**Critical**: The EVOLVE function must appear EXACTLY as provided, between the markers. Do NOT modify its logic.

Output ONLY the complete Python source code, no explanations or markdown fences.
"""

CREATE_TASK_FROM_DRIVER_PROMPT = """\
You are an expert Python developer building an LLM4AD task from a generated driver script.

## Task Specification
- Project: {project_name}
- Background: {background}
- Function to evolve: {function_name}
- Metrics: {metrics_json}
- Algorithm directory: {algorithm_dir_name}
- Algorithm file: {algorithm_file_name}

## Generated Driver Script (the algorithm file)
```python
{driver_code}
```

## Input/Output Schema
Input schema: {input_schema_json}
Output schema: {output_schema_json}

## LLM4AD Evaluator Template
```python
{evaluator_template}
```

## Requirements

### Evaluator Requirements:
1. Subclass `BaseEvaluator` and register with `@BaseEvaluator.register("{evaluator_register_name}")`
2. Define metrics in `__init__` matching the specification above
3. Implement async `evaluate(self, cfg: EvalContext) -> EvaluationResult`
4. Use subprocess isolation: spawn the algorithm file as a subprocess
5. The algorithm file has a `main()` that reads JSON from sys.argv[1] and prints JSON result
6. Handle worktree compatibility: check both nested and flat directory layouts
7. Parse JSON output from subprocess, compute score (higher is always better for evolution)
8. Import from `llm4ad.evaluator.base`: BaseEvaluator, EvalContext, EvaluationResult, Metric, MetricType

### Debug Runner Requirements:
Generate a simple `debug_run.py` script that:
1. Creates a small sample input matching the input schema
2. Runs the algorithm on it directly (no subprocess)
3. Prints the result for quick validation

### Sample Data Requirements (CRITICAL):
You MUST generate a valid JSON sample data instance matching `input_schema`.
- Must be a realistic, minimal example
- NEVER output "NONE" or leave it empty

### Test Evaluator Requirements:
Generate a `test_evaluator.py` that exercises the evaluator end-to-end.
Pattern: import evaluator class, create EvalContext pointing to
data/sample/instance_001.json, call evaluate(), check success and metrics,
print [PASS] or [FAIL].

## Output Format
Output exactly these sections:

===EVALUATOR_CODE===
<complete Python source for the evaluator file>

===DEBUG_RUN===
<complete Python source for debug_run.py>

===TEST_EVALUATOR===
<complete Python source for test_evaluator.py>

===SAMPLE_DATA===
<Valid JSON content matching input_schema. Must be valid JSON parseable by json.loads().>

===METADATA===
<JSON object with: {{"evaluator_class_name": "...", "evaluator_register_name": "..."}}>
"""

ANALYZE_MULTIMODAL_SUPPLEMENT = """\

## Multimodal Visualization

This task requires a **multimodal evaluator** that generates visualization images
alongside metrics. In addition to the fields above, also include:

```json
{{
    "visualization_spec": {{
        "renderer_name": "<snake_case_name for the BaseRenderer subclass, e.g. tsp_tour>",
        "visualization_label": "<human-readable label for the image, e.g. TSP Tour Map>",
        "description": "<what the visualization shows and why it helps the LLM>",
        "raw_data_schema": {{
            "<field_name>": "<type and description>"
        }}
    }}
}}
```

{user_viz_hint}

Guidelines for visualization_spec:
- The renderer_name will be used as `@BaseRenderer.register("<renderer_name>")`
- The visualization should show algorithm behavior (search trajectory, solution structure, etc.)
- raw_data_schema describes the compact data stored for deferred rendering
- Choose a visualization that helps the LLM diagnose and improve the algorithm
"""

# ---------------------------------------------------------------------------
# Creation prompts
# ---------------------------------------------------------------------------

CREATE_TASK_PROMPT = """\
You are an expert Python developer building an LLM4AD algorithm evolution task.
Generate the evaluator code and algorithm template based on the specification below.

## Task Specification
- Project: {project_name}
- Background: {background}
- Function to evolve: {function_name}
- Signature: {function_signature}
- Description: {function_description}
- Input format: {input_format}
- Output format: {output_format}
- Metrics: {metrics_json}
- Algorithm directory: {algorithm_dir_name}
- Algorithm file: {algorithm_file_name}

## LLM4AD Evaluator Template (follow this pattern exactly)
```python
{evaluator_template}
```

## LLM4AD Algorithm Template (follow this pattern exactly)
```python
{algorithm_template}
```

## Requirements

### Evaluator Requirements:
1. Subclass `BaseEvaluator` and register with `@BaseEvaluator.register("{evaluator_register_name}")`
2. Define metrics in `__init__` matching the specification above
3. Implement async `evaluate(self, cfg: EvalContext) -> EvaluationResult`
4. Use subprocess isolation: spawn this file's `__main__` block as a subprocess
5. The `_run_algorithm` static method loads the algorithm module via `importlib.util`
6. Handle worktree compatibility: check both nested and flat directory layouts
7. Parse JSON output from subprocess, compute score (higher is always better for evolution)
8. Import from `llm4ad.evaluator.base`: BaseEvaluator, EvalContext, EvaluationResult, Metric, MetricType

### Algorithm Requirements:
1. Include `EVOLVE_START` and `EVOLVE_END` markers around the evolvable function
2. The evolvable function must match the specified signature
3. Include a `process()` wrapper that calls the function and formats output as dict
4. Include a `main()` entry point that reads JSON from sys.argv[1] and prints JSON result
5. Keep imports inside the EVOLVE markers if they're only used by the evolvable function
6. Provide a reasonable baseline implementation (not just `pass` or `return None`)

### Debug Runner Requirements:
Generate a simple `debug_run.py` script that:
1. Creates a small sample input matching the task's data format
2. Runs the algorithm on it directly (no subprocess)
3. Prints the result for quick validation

### Sample Data Requirements (CRITICAL):
You MUST generate a valid JSON sample data instance in the SAMPLE_DATA section.
This is required for the pipeline to validate the evaluator at build time.
- Must match the input format specification above
- Must be a realistic, minimal example (one instance is enough)
- NEVER output "NONE" or leave it empty — the build will fail without sample data

### Test Evaluator Requirements:
Generate a `test_evaluator.py` script that exercises the *evaluator* (not just the
algorithm) end-to-end. This is critical to verify the evaluator actually evaluates
correctly, not merely that it doesn't error. Pattern:

```python
import asyncio
from pathlib import Path
from {{evaluator_module_name}} import {{evaluator_class_placeholder}}
from llm4ad.config.schema import EvalContext


async def test_evaluator():
    current_dir = Path(__file__).parent
    data_path = current_dir / "data" / "sample" / "instance_001.json"
    if not data_path.exists():
        print(f"[X] Data file not found: {{data_path}}")
        return False
    evaluator = {{evaluator_class_placeholder}}()
    cfg = EvalContext(
        data_path=str(data_path),
        project_root=str(current_dir),
        timeout=120.0,
    )
    result = await evaluator.evaluate(cfg)
    print(f"Success: {{result.success}}")
    print(f"Score: {{result.score}}")
    print(f"Metrics: {{result.metrics}}")
    if result.error_message:
        print(f"Error: {{result.error_message}}")
    expected_metrics = [<list of metric names from spec>]
    has_all = all(m in result.metrics for m in expected_metrics)
    if result.success and has_all:
        print("[PASS] Test PASSED")
        return True
    print("[FAIL] Test FAILED")
    return False


if __name__ == "__main__":
    success = asyncio.run(test_evaluator())
    exit(0 if success else 1)
```

Replace `{{evaluator_module_name}}` with the evaluator file's module name (without
.py) and `{{evaluator_class_placeholder}}` with the actual class name. Populate
`expected_metrics` with the metric names from the spec.

## Output Format
Output exactly these sections with the delimiters shown:

===EVALUATOR_CODE===
<complete Python source for the evaluator file>

===ALGORITHM_CODE===
<complete Python source for the algorithm file>

===DEBUG_RUN===
<complete Python source for debug_run.py>

===TEST_EVALUATOR===
<complete Python source for test_evaluator.py>

===SAMPLE_DATA===
<Valid JSON content for a sample data file. REQUIRED: always produce a valid, minimal but
realistic JSON instance matching the input format. Must be valid JSON parseable by json.loads()
— no comments, no trailing commas, no multi-line strings with unescaped newlines. Never output
NONE or empty — the task pipeline cannot validate without sample data.>

===METADATA===
<JSON object with: {{"evaluator_class_name": "...", "evaluator_register_name": "..."}}>
"""

CREATE_TASK_MULTIMODAL_PROMPT = """\
You are an expert Python developer building an LLM4AD **multimodal** algorithm evolution task.
Generate the evaluator code and algorithm template based on the specification below.
The evaluator must produce visualization images alongside metrics.

## Task Specification
- Project: {project_name}
- Background: {background}
- Function to evolve: {function_name}
- Signature: {function_signature}
- Description: {function_description}
- Input format: {input_format}
- Output format: {output_format}
- Metrics: {metrics_json}
- Algorithm directory: {algorithm_dir_name}
- Algorithm file: {algorithm_file_name}

## Visualization Specification
{visualization_spec_json}

## LLM4AD Multimodal Evaluator Template (follow this pattern exactly)
```python
{evaluator_template}
```

## LLM4AD Algorithm Template (follow this pattern exactly)
```python
{algorithm_template}
```

## Requirements

### Evaluator Requirements (Multimodal):
1. Subclass `BaseEvaluator` and register with `@BaseEvaluator.register("{evaluator_register_name}")`
2. Define metrics in `__init__` matching the specification above
3. Implement async `evaluate(self, cfg: EvalContext) -> EvaluationResult`
4. Use subprocess isolation: spawn this file's `__main__` block as a subprocess
5. The `_run_algorithm` method loads the algorithm module via `importlib.util`
6. Handle worktree compatibility: check both nested and flat directory layouts
7. Parse JSON output from subprocess, compute score (higher is always better for evolution)
8. Import from `llm4ad.evaluator.base`: BaseEvaluator, EvalContext, EvaluationResult, Metric, MetricType
9. Import from `llm4ad.evaluator.behavior`: BehaviorData, BehaviorVisualization
10. Import from `llm4ad.evaluator.renderer`: BaseRenderer
11. Implement `_render_result_image()` function that produces a base64 PNG visualization
12. Implement `_build_observation_text()` function that produces LLM-readable text summary
13. Register a `BaseRenderer` subclass with `@BaseRenderer.register(...)` for deferred rendering
14. Handle `cfg.behavior_storage` modes in evaluate():
    - "rendered": subprocess renders image, returns image_base64
    - "raw": subprocess returns compact raw data for deferred rendering
    - "none": no behavior data saved
15. Build `BehaviorData` with observation text + `BehaviorVisualization`
16. Subprocess `_subprocess_main()` must handle behavior_storage modes

### Algorithm Requirements:
1. Include `EVOLVE_START` and `EVOLVE_END` markers around the evolvable function
2. The evolvable function must match the specified signature
3. Include a `process()` wrapper that calls the function and formats output as dict
4. Include a `main()` entry point that reads JSON from sys.argv[1] and prints JSON result
5. Keep imports inside the EVOLVE markers if they're only used by the evolvable function
6. Provide a reasonable baseline implementation (not just `pass` or `return None`)

### Debug Runner Requirements:
Generate a simple `debug_run.py` script that:
1. Creates a small sample input matching the task's data format
2. Runs the algorithm on it directly (no subprocess)
3. Prints the result for quick validation

### Sample Data Requirements (CRITICAL):
You MUST generate a valid JSON sample data instance in the SAMPLE_DATA section.
This is required for the pipeline to validate the evaluator at build time.
- Must match the input format specification above
- Must be a realistic, minimal example (one instance is enough)
- NEVER output "NONE" or leave it empty — the build will fail without sample data

### Test Evaluator Requirements:
Generate a `test_evaluator.py` script that exercises the *evaluator* (not just the
algorithm) end-to-end. See the non-multimodal prompt above for the full pattern.
The test must:
1. Import the evaluator class from the evaluator module
2. Create an `EvalContext` pointing to `data/sample/instance_001.json`
3. IMPORTANT for multimodal: pass `behavior_storage="rendered"` to `EvalContext`
4. Call `await evaluator.evaluate(cfg)` and check `result.success`
5. Verify all expected metric names are present in `result.metrics`
6. Print `[PASS]` or `[FAIL]` and exit 0 or 1

## Output Format
Output exactly these sections with the delimiters shown:

===EVALUATOR_CODE===
<complete Python source for the multimodal evaluator file>

===ALGORITHM_CODE===
<complete Python source for the algorithm file>

===DEBUG_RUN===
<complete Python source for debug_run.py>

===TEST_EVALUATOR===
<complete Python source for test_evaluator.py>

===SAMPLE_DATA===
<Valid JSON content for a sample data file. REQUIRED: always produce a valid, minimal but
realistic JSON instance matching the input format. Must be valid JSON parseable by json.loads()
— no comments, no trailing commas, no multi-line strings with unescaped newlines. Never output
NONE or empty — the task pipeline cannot validate without sample data.>

===METADATA===
<JSON object with: {{"evaluator_class_name": "...", "evaluator_register_name": "..."}}>
"""

# ---------------------------------------------------------------------------
# Creation prompt for reusing existing algorithm code (from_code with EVOLVE)
# ---------------------------------------------------------------------------

CREATE_TASK_REUSE_ALGORITHM_PROMPT = """\
You are an expert Python developer building an LLM4AD algorithm evolution task.
The user has an existing codebase with EVOLVE markers. The algorithm file will
be assembled separately — you only need to generate the evaluator, debug runner,
test, and sample data.

## Task Specification
- Project: {project_name}
- Background: {background}
- Function to evolve: {function_name}
- Signature: {function_signature}
- Description: {function_description}
- Input format: {input_format}
- Output format: {output_format}
- Metrics: {metrics_json}
- Algorithm directory: {algorithm_dir_name}
- Algorithm file: {algorithm_file_name}

## Original File (CONTEXT ONLY — understand how the function is used)
The full original file is shown below so you can understand how the function
is called, what data it receives, and what it returns. It may contain old APIs
or dependencies that must NOT appear in the generated files.
```python
{existing_algorithm_code}
```

## EVOLVE Block (the function being evolved)
```python
{evolve_block_content}
```

## Generated Algorithm File Structure
The algorithm file has already been assembled as a standalone script with:
- The EVOLVE function between `# EVOLVE_START` and `# EVOLVE_END`
- A `solve(input_data)` wrapper that calls `{function_name}(**input_data)` and returns the result
- A `main()` entry point that reads JSON from sys.argv[1] and prints JSON result
- Can be run as: `python {algorithm_file_name} '<json_input>'`

The evaluator must spawn this file as a subprocess and parse its JSON output.

## LLM4AD Evaluator Template (follow this pattern exactly)
```python
{evaluator_template}
```

## Requirements

### Evaluator Requirements:
1. Subclass `BaseEvaluator` and register with `@BaseEvaluator.register("{evaluator_register_name}")`
2. Define metrics in `__init__` matching the specification above
3. Implement async `evaluate(self, cfg: EvalContext) -> EvaluationResult`
4. Use subprocess isolation: spawn the algorithm file as a subprocess
5. Handle worktree compatibility: check both nested and flat directory layouts
6. Parse JSON output from subprocess, compute score (higher is always better for evolution)
7. Import from `llm4ad.evaluator.base`: BaseEvaluator, EvalContext, EvaluationResult, Metric, MetricType

### Debug Runner Requirements:
Generate a simple `debug_run.py` script that:
1. Creates a small sample input matching the task's data format
2. Runs the algorithm on it directly (no subprocess)
3. Prints the result for quick validation

### Sample Data Requirements (CRITICAL):
You MUST generate a valid JSON sample data instance in the SAMPLE_DATA section.
- Must match the input format that the algorithm expects
- Must be a realistic, minimal example
- NEVER output "NONE" or leave it empty

### Test Evaluator Requirements:
Generate a `test_evaluator.py` that exercises the evaluator end-to-end.
Pattern: import evaluator class, create EvalContext pointing to
data/sample/instance_001.json, call evaluate(), check success and metrics,
print [PASS] or [FAIL].

## Output Format
Output exactly these sections (NO ===ALGORITHM_CODE=== section — it is pre-built):

===EVALUATOR_CODE===
<complete Python source for the evaluator file>

===DEBUG_RUN===
<complete Python source for debug_run.py>

===TEST_EVALUATOR===
<complete Python source for test_evaluator.py>

===SAMPLE_DATA===
<Valid JSON content for a sample data file. Must be valid JSON parseable by json.loads().>

===METADATA===
<JSON object with: {{"evaluator_class_name": "...", "evaluator_register_name": "..."}}>
"""

CONFIG_YAML_TEMPLATE = """\
# ==========================================================================
# LLM4AD Task Configuration — Auto-generated by llm4ad build
# ==========================================================================

project_name: "{project_name}"

background: |
{background_indented}

base_dir: "./runs"
random_seed: 42

# ===== LLM Provider Configuration =====
providers:
  - name: "default"
    type: "openai_compatible"
    base_url: "${{LLM_BASE_URL}}"
    api_key: "${{LLM_API_KEY}}"
    model: "${{LLM_MODEL}}"
    temperature: 0.7
    max_tokens: 4096
    timeout: 120.0

{multimodal_config_yaml}# ===== Evaluator Configuration =====
evaluator:
  type: "custom"
  module: "{evaluator_module}"
  timeout: 60.0
  max_retries: 2
  parallel: true
  batch_size: 5
  dataset:
{dataset_yaml}
  metrics: {metrics_list}

{evolution_yaml}
# ===== Planner Configuration =====
planner:
  type: "llm_evolution"
  provider: "default"
  selection_strategy: "weighted"
  parent_selection_strategy: "tournament"
  samplers:
    - name: "init_sampler"
{planner_samplers_yaml}

# ===== Coder Configuration =====
coder:
  type: "custom"
  provider: "default"
  prompt_template: |
{prompt_template_indented}

# ===== Memory Configuration =====
memory:
  embedding_dim: 768
  max_entries: 1000
  similarity_threshold: 0.8
  decay_factor: 0.99

# ===== Logging Configuration =====
logging:
  level: "INFO"
  console: true
  json_format: false

# ===== Version Control Configuration =====
version_control:
  enabled: true
  type: "git_worktree"
  local_path: "./{algorithm_dir_name}"
  auto_initialize: true
  auto_cleanup: true

# ===== Repository Analyzer Configuration =====
repo_analyzer:
  type: "evolve_detector"
  context_lines_before: 5
  context_lines_after: 5
  include: ["*.py"]
  exclude: [".git/**", "__pycache__/**", "*.pyc"]
"""

# ---------------------------------------------------------------------------
# Coder prompt template (embedded in config YAML)
# ---------------------------------------------------------------------------

CODER_PROMPT_TEMPLATE = """\
You are tasked with implementing an algorithm for: {task_description}

{background}

Your task is to implement the function below. Only modify code between
EVOLVE_START and EVOLVE_END markers.

```python
{algorithm_code_for_prompt}
```

Requirements:
1. Implement the `{function_name}` function between EVOLVE_START and EVOLVE_END
2. Input format: {input_format}
3. Output format: {output_format}
4. Optimization goals: {optimization_goals}

Provide only the complete implementation of the {function_name} function
between EVOLVE_START and EVOLVE_END markers.\
"""

# ---------------------------------------------------------------------------
# Repair prompts
# ---------------------------------------------------------------------------

REPAIR_EVALUATOR_PROMPT = """\
The following LLM4AD evaluator code has an error. Fix it.

## Current Code
```python
{evaluator_code}
```

## Latest Error
{error_message}

{history_section}
## Requirements
1. Must subclass `BaseEvaluator` from `llm4ad.evaluator.base`
2. Must use `@BaseEvaluator.register(...)` decorator
3. Must define `_metrics` in `__init__` and expose via `metrics` property
4. Must implement async `evaluate(self, cfg: EvalContext) -> EvaluationResult`
5. Must use subprocess isolation (spawn self as subprocess via `__main__`)
6. Must handle worktree paths (check both nested and flat directory layouts)
7. The `_run_algorithm` static method must load algorithm via `importlib.util`

## Reference Template
```python
{evaluator_template}
```

Output ONLY the fixed complete Python source code. No explanations.
"""

REPAIR_ALGORITHM_PROMPT = """\
The following LLM4AD algorithm file has an error. Fix it.

## Current Code
```python
{algorithm_code}
```

## Latest Error
{error_message}

{history_section}
## Requirements
1. Must have EVOLVE_START and EVOLVE_END markers
2. The evolvable function must be between the markers
3. Must have a `process()` wrapper that returns a dict
4. Must have a `main()` entry point reading JSON from sys.argv[1]
5. Must print JSON result to stdout
6. Must provide a working baseline implementation

## Reference Template
```python
{algorithm_template}
```

Output ONLY the fixed complete Python source code. No explanations.
"""

REPAIR_DATASET_PROMPT = """\
The generated LLM4AD task is missing sample data for testing.

## Task Specification
- Project: {project_name}
- Function: {function_name}
- Input format: {input_format}
- Output format: {output_format}

## Algorithm Code (for reference)
```python
{algorithm_code}
```

Generate a valid JSON sample data instance that can be used to test the algorithm.
The data must match the input format specification above.

CRITICAL: Output ONLY valid JSON that can be parsed by Python's json.loads().
- No comments (// or #)
- No trailing commas
- No code fences or markdown
- No explanations or prose
- Must be a single valid JSON value (object, array, string, number, etc.)
"""

REPAIR_DEBUG_RUN_PROMPT = """\
The following LLM4AD debug_run.py script has an error. Fix it.

## Current Code
```python
{debug_run_code}
```

## Latest Error
{error_message}

{history_section}
## Algorithm Info
- Algorithm directory: {algorithm_dir_name}/
- Algorithm file: {algorithm_dir_name}/{algorithm_file_name}
- Function to evolve: {function_name}

## Algorithm Code (for reference)
```python
{algorithm_code}
```

## Requirements
1. Import the function using sys.path:
   ```python
   import sys
   from pathlib import Path
   sys.path.insert(0, str(Path(__file__).parent / "{algorithm_dir_name}"))
   from {algorithm_module} import {function_name}
   ```
2. Must run a quick test with sample data and print results
3. Must exit with code 0 on success
4. All parentheses, brackets, quotes, and triple-quotes must be properly closed
5. Keep it simple — no complex test frameworks, just call the function and print

Output ONLY the fixed complete Python source code. No explanations.
"""

REPAIR_FULL_PROMPT = """\
The generated LLM4AD task has persistent errors after multiple repair attempts.
Please regenerate both the evaluator and algorithm from scratch.

## Task Specification
- Project: {project_name}
- Function: {function_name} ({function_signature})
- Description: {function_description}
- Metrics: {metrics_json}
- Algorithm dir: {algorithm_dir_name}
- Algorithm file: {algorithm_file_name}

## Previous Errors
{error_history}

## Evaluator Template
```python
{evaluator_template}
```

## Algorithm Template
```python
{algorithm_template}
```

Output exactly these sections:

===EVALUATOR_CODE===
<complete evaluator Python source>

===ALGORITHM_CODE===
<complete algorithm Python source>

===DEBUG_RUN===
<complete debug_run.py source>

===TEST_EVALUATOR===
<complete test_evaluator.py source>
"""

REPAIR_TEST_EVALUATOR_PROMPT = """\
The following LLM4AD test_evaluator.py script failed. Fix it.

## Current Code
```python
{test_evaluator_code}
```

## Latest Error / Output
{error_message}

{history_section}
## Context
- Evaluator file: {evaluator_file_name}
- Evaluator class: {evaluator_class_name}
- Expected metrics: {metric_names}
- Sample data path: data/sample/instance_001.json

## Requirements
1. Must import `{evaluator_class_name}` from the evaluator module (filename without .py)
2. Must import `EvalContext` from `llm4ad.config.schema`
3. Must use `asyncio.run(test_evaluator())` in `__main__`
4. Must build `EvalContext(data_path=..., project_root=..., timeout=120.0)` pointing
   to `Path(__file__).parent / "data" / "sample" / "instance_001.json"`
5. If the evaluator is multimodal (accesses `cfg.behavior_storage`), you MUST pass
   `behavior_storage="rendered"` to `EvalContext`. Otherwise omit it.
6. Must `await evaluator.evaluate(cfg)` and check `result.success`
7. Must verify that all expected metrics appear in `result.metrics`
8. Must print `[PASS]` and `exit(0)` on success, `[FAIL]` and `exit(1)` on failure
9. All parentheses, brackets, quotes, and triple-quotes must be properly closed

Note: if the failure is due to the *evaluator itself* being broken (not the test
script), still produce a correct test_evaluator.py — the evaluator will be fixed
in a separate pass.

Output ONLY the fixed complete Python source code. No explanations.
"""

REPAIR_EVALUATOR_MULTIMODAL_PROMPT = """\
The following LLM4AD **multimodal** evaluator code has an error. Fix it.

## Current Code
```python
{evaluator_code}
```

## Latest Error
{error_message}

{history_section}
## Requirements
1. Must subclass `BaseEvaluator` from `llm4ad.evaluator.base`
2. Must use `@BaseEvaluator.register(...)` decorator
3. Must define `_metrics` in `__init__` and expose via `metrics` property
4. Must implement async `evaluate(self, cfg: EvalContext) -> EvaluationResult`
5. Must use subprocess isolation (spawn self as subprocess via `__main__`)
6. Must handle worktree paths (check both nested and flat directory layouts)
7. Must import `BehaviorData`, `BehaviorVisualization` from `llm4ad.evaluator.behavior`
8. Must import `BaseRenderer` from `llm4ad.evaluator.renderer`
9. Must register a `BaseRenderer` subclass with `@BaseRenderer.register(...)`
10. Must implement `_render_result_image()` for visualization
11. Must implement `_build_observation_text()` for LLM-readable summaries
12. Must handle `cfg.behavior_storage` modes ("rendered"/"raw"/"none")
13. Must build `BehaviorData` with observation text + `BehaviorVisualization`

## Reference Template
```python
{evaluator_template}
```

Output ONLY the fixed complete Python source code. No explanations.
"""


REQUIREMENTS_PROMPT = """\
You are preparing a `requirements.txt` for a generated LLM4AD algorithm-design
project. The user will run an evolutionary loop that mutates the algorithm
many times — so the dependency list must cover not only what the BASELINE code
imports today, but also what alternative approaches for this same problem are
LIKELY to need after a few generations of evolution. The cost of including a
spare package (small) is much lower than the cost of a mid-run pip install.

IMPORTANT: Pay close attention to the task description below. Look for domain-specific
keywords (e.g., "image", "vision", "text", "graph", "time series") and include
corresponding packages even if the baseline code doesn't import them yet — evolution
may discover better approaches that need them.

## Task Description
{task_description}

## Analysis
- Problem type: {problem_type}
- Complexity tier: {complexity_tier}

## Baseline Evaluator Code
```python
{evaluator_code}
```

## Baseline Algorithm Code
```python
{algorithm_code}
```

## Test Evaluator Code
```python
{test_evaluator_code}
```

## What to include
1. **Currently-imported packages**: every third-party package the code above
   actually imports right now.
2. **Task-description-driven packages**: scan the task description for domain
   keywords and include relevant packages (see keyword hints below).
3. **Problem-type-driven packages**: use the cheat sheet below as a baseline
   for the declared problem_type.
4. **Evolution-anticipation packages**: packages a competent practitioner would
   reach for if they re-implemented this problem with a different but
   reasonable approach.

### Cheat sheet by problem_type
- `combinatorial_optimization`: numpy, scipy, networkx, ortools, pulp, mip, numba
- `sorting`: numpy
- `scheduling`: numpy, scipy, networkx, ortools, pandas, mip
- `ml`: numpy, pandas, scikit-learn, scipy, matplotlib, joblib
- `rl`: numpy, scipy, gymnasium, torch, matplotlib, stable-baselines3
- `regression`: numpy, pandas, scikit-learn, scipy, statsmodels, matplotlib
- `simulation`: numpy, scipy, matplotlib, pandas, numba
- `computer_vision`: numpy, opencv-python, Pillow, scikit-image, torch, torchvision, matplotlib
- `image_processing`: numpy, opencv-python, Pillow, scikit-image, matplotlib, scipy
- `nlp`: numpy, torch, transformers, tokenizers, nltk, spacy, scikit-learn
- `text_processing`: numpy, pandas, nltk, spacy, scikit-learn
- `deep_learning`: numpy, torch, torchvision, matplotlib, scipy, tqdm
- `graph_processing`: numpy, networkx, scipy, matplotlib
- `data_analysis`: numpy, pandas, matplotlib, seaborn, scipy, scikit-learn
- `time_series`: numpy, pandas, scipy, statsmodels, matplotlib, scikit-learn
- `visualization`: matplotlib, seaborn, plotly, pandas, numpy
- `other`: numpy, scipy, matplotlib (only the obviously useful ones)

### Task description keyword hints (add these if keywords appear in task_description):
- Keywords "image", "vision", "visual", "picture", "photo", "camera", "pixel", "cv":
  ADD opencv-python, Pillow, scikit-image
- Keywords "video", "frame", "stream": ADD opencv-python
- Keywords "text", "language", "nlp", "sentiment", "translation", "summarization":
  ADD transformers, tokenizers (or nltk, spacy for simpler tasks)
- Keywords "plot", "chart", "graph" (visualization context), "dashboard":
  ADD matplotlib, seaborn (or plotly for interactive)
- Keywords "neural", "deep learning", "cnn", "rnn", "lstm", "transformer", "diffusion":
  ADD torch, torchvision (if vision-related)
- Keywords "time series", "forecast", "trend", "seasonal":
  ADD pandas, statsmodels
- Keywords "network", "node", "edge", "topology", "centrality":
  ADD networkx
- Keywords "parallel", "multiprocess", "distributed":
  ADD joblib (or ray for heavier workloads)

For RL tasks specifically: include the env backend (e.g. `gymnasium[box2d]`,
`gymnasium[atari]`, `gymnasium[mujoco]` based on task description keywords)
AND a deep-learning framework (`torch` is the safest default) AND `scipy`
since policy/optimization variants commonly reach for it. If the task mentions
"image" or "visual" observations, ADD opencv-python and Pillow.

For complexity_tier='complex', err on the side of including a few extras
(numba for hot loops, joblib for parallelism, matplotlib for diagnostics,
tqdm for progress tracking).

## Hard rules
1. Output one package per line, in pip-installable form (e.g. `numpy`, `numpy>=1.24`,
   `gymnasium[box2d]`). No comments, no blank lines, no prose, no code fences.
2. EXCLUDE the Python standard library (e.g. `os`, `json`, `subprocess`, `pathlib`,
   `typing`, `dataclasses`, `asyncio`, `re`, `math`, `random`, `itertools`,
   `functools`, `collections`, `hashlib`, `logging`, `tempfile`, `shutil`,
   `importlib`, `concurrent`, `multiprocessing`, `threading`, `time`, `datetime`,
   `io`, `copy`, `ast`, `inspect`, `argparse`, `enum`, `abc`, `uuid`, `csv`,
   `pickle`, `warnings`, `traceback`, `sqlite3`).
3. EXCLUDE `llm4ad` itself — the project depends on the parent package, not on PyPI.
4. Use a permissive lower-bound version constraint (`>=X.Y`) when a recent feature
   is clearly needed; otherwise output the bare package name. Never pin an exact
   version (`==`) unless the task description explicitly requires it.
5. Map common import names to their PyPI names:
   `cv2` -> `opencv-python`, `PIL` -> `Pillow`, `sklearn` -> `scikit-learn`,
   `yaml` -> `PyYAML`, `serial` -> `pyserial`, `bs4` -> `beautifulsoup4`,
   `gym` -> `gymnasium` (prefer the maintained fork unless the code clearly
   relies on legacy `gym`).
6. Cap the list at ~20 entries. If the cheat sheet would push you past that,
   keep the most directly relevant ones for this specific problem and drop
   the more speculative entries.
7. Deduplicate. Sort alphabetically.

If the project genuinely needs no third-party packages (only stdlib + llm4ad),
output the single line: `# none`

Output now:
"""


def get_evaluator_template() -> str:
    """Load the evaluator template as a string for few-shot prompts."""
    return _load_example("my_evaluator.py")


def get_algorithm_template() -> str:
    """Load the algorithm template as a string for few-shot prompts."""
    return _load_example("my_algorithm/my_function.py")


def get_multimodal_evaluator_template() -> str:
    """Load the multimodal evaluator template as a string for few-shot prompts."""
    return _load_multimodal_example("my_evaluator.py")


def get_multimodal_algorithm_template() -> str:
    """Load the multimodal algorithm template as a string for few-shot prompts."""
    return _load_multimodal_example("my_algorithm/my_function.py")
