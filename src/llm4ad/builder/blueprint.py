"""Data models for the automated task builder pipeline.

Defines TaskBlueprint (the data contract flowing between all builder agents)
and AnalysisResult (output of TaskAnalyzer, input to TaskCreator).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AnalysisResult:
    """Structured problem analysis from TaskAnalyzer.

    Produced by TaskAnalyzer, consumed by TaskCreator.
    Contains all information needed to generate application artifacts.
    """

    problem_type: str
    """Category: combinatorial_optimization, sorting, ml, rl, regression, etc."""

    evaluation_pattern: str
    """Evaluation contract for the evolved function:

    - ``separate_script`` (Variant A): the evaluator spawns
      ``python algo.py '<instance>'`` for a single one-shot call; the algorithm
      is a stateless solver with a ``main()`` reading ``sys.argv``.
    - ``self_spawn`` (Variant B): the evaluator spawns ITSELF as a subprocess and
      drives an environment/simulation loop that calls a policy function many
      times per episode; the instance is a seed, not the full input.

    Normalized to one of these two values by TaskAnalyzer.
    """

    function_name: str
    """Proposed name for the function to evolve (e.g., 'greedy_coloring')."""

    function_signature: str
    """Full Python signature (e.g., 'def greedy_coloring(graph: dict) -> dict:')."""

    function_description: str
    """What the function should do, for the LLM prompt."""

    metrics: list[dict[str, Any]]
    """Proposed metrics: [{name, type, weight, description}]."""

    input_format: str
    """Description of the input data format the algorithm receives."""

    output_format: str
    """Description of the expected output format."""

    algorithm_dir_name: str
    """Proposed directory name (e.g., 'graph_coloring_algorithm')."""

    algorithm_file_name: str
    """Proposed file name (e.g., 'coloring.py')."""

    complexity_tier: str = "simple"
    """Estimated problem complexity: 'simple', 'medium', or 'complex'.

    Inferred by the LLM during analysis. Used by ConfigRecommender to
    select evolution hyperparameters. Defaults to 'simple' when the LLM
    cannot determine complexity.
    """

    project_name: str = ""
    """Proposed project name slug."""

    background: str = ""
    """Background description for the YAML config."""

    existing_code_summary: str | None = None
    """Summary of existing codebase (from_code mode)."""

    existing_code_files: dict[str, str] | None = None
    """Content of key existing code files (from_code mode)."""

    dataset_summary: str | None = None
    """Summary of dataset structure (if data_path provided)."""

    visualization_spec: dict[str, Any] | None = None
    """Visualization specification for multimodal evaluators."""

    has_evolve_markers: bool = False
    """Whether EVOLVE markers were detected in the provided code (from_code mode)."""

    algorithm_full_code: str | None = None
    """Complete content of the algorithm file containing EVOLVE markers."""

    evolve_block_content: str | None = None
    """Source code between EVOLVE_START and EVOLVE_END markers."""

    function_role: str = "complete_solver"
    """Semantic role of the EVOLVE function: complete_solver | sub_function | helper."""

    input_schema: dict[str, Any] | None = None
    """JSON schema describing the input the algorithm file expects."""

    output_schema: dict[str, Any] | None = None
    """JSON schema describing the output the algorithm file produces."""

    needed_helpers: list[str] = field(default_factory=list)
    """Names of functions/classes from original file needed by the EVOLVE function."""

    driver_strategy: str = ""
    """LLM-written description of how the driver wires the EVOLVE function."""


@dataclass
class TaskBlueprint:
    """Central data contract flowing between all builder agents.

    Equivalent to AutoEoH's TaskPackage, but stores LLM4AD-native
    artifacts: evaluator code, algorithm code with EVOLVE markers,
    YAML config, and a debug runner.
    """

    # --- Identity ---
    project_name: str
    task_description: str

    # --- Generated file contents ---
    evaluator_code: str
    algorithm_code: str
    config_yaml: str
    debug_run_code: str

    # --- Structured metadata ---
    evaluator_class_name: str
    evaluator_file_name: str
    algorithm_dir_name: str
    algorithm_file_name: str
    function_to_evolve: str
    metrics: list[dict[str, Any]]

    # --- Optional ---
    evaluation_pattern: str = "separate_script"
    """Evaluation contract: 'separate_script' (Variant A, one-shot
    `python algo.py '<instance>'`) or 'self_spawn' (Variant B, the evaluator
    spawns itself and drives an environment loop calling a policy function
    many times per episode). Determines algorithm template, sample-data
    generation, and how the validator exercises the algorithm."""
    dataset_files: dict[str, str] = field(default_factory=dict)
    source_code_path: str | None = None
    test_evaluator_code: str = ""
    requirements_txt: str = ""
    """Pip-installable dependencies as requirements.txt content.

    Generated after validation by an LLM pass that inspects the produced
    evaluator/algorithm code and the task description. One package per line,
    optionally with a version constraint. Empty when generation was skipped
    or returned nothing usable.
    """

    # --- Reuse mode ---
    reuse_algorithm: bool = False
    """Whether algorithm code is user-provided and must not be regenerated."""

    # --- Validation state ---
    validation_status: str = "pending"
    validation_errors: list[str] = field(default_factory=list)
    repair_attempts: int = 0

    def is_valid(self) -> bool:
        """Check if the blueprint has passed validation."""
        return self.validation_status == "passed"

    def save(self, output_dir: str | Path) -> Path:
        """Serialize blueprint metadata to JSON.

        Args:
            output_dir: Directory to write metadata file.

        Returns:
            Path to the metadata JSON file.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        meta_path = output_dir / "blueprint_meta.json"
        meta = {
            "project_name": self.project_name,
            "task_description": self.task_description,
            "evaluator_class_name": self.evaluator_class_name,
            "evaluator_file_name": self.evaluator_file_name,
            "algorithm_dir_name": self.algorithm_dir_name,
            "algorithm_file_name": self.algorithm_file_name,
            "function_to_evolve": self.function_to_evolve,
            "metrics": self.metrics,
            "evaluation_pattern": self.evaluation_pattern,
            "validation_status": self.validation_status,
            "validation_errors": self.validation_errors,
            "repair_attempts": self.repair_attempts,
            "has_requirements": bool(self.requirements_txt.strip()),
        }
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return meta_path
