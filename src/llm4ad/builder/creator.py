"""TaskCreator agent: generate all LLM4AD application artifacts.

Takes an AnalysisResult from TaskAnalyzer and produces a complete
TaskBlueprint with evaluator code, algorithm code, YAML config,
and debug runner.
"""

from __future__ import annotations

import ast
import json
import re
import textwrap

from loguru import logger

from llm4ad.builder.blueprint import AnalysisResult, TaskBlueprint
from llm4ad.builder.config_recommender import render_default_evolution_yaml
from llm4ad.builder.prompts import (
    CODER_PROMPT_TEMPLATE,
    CONFIG_YAML_TEMPLATE,
    CREATE_ALGORITHM_POLICY_PROMPT,
    CREATE_ALGORITHM_PROMPT,
    CREATE_EVALUATOR_FROM_DRIVER_PROMPT,
    CREATE_EVALUATOR_PROMPT,
    CREATE_EVALUATOR_SELF_SPAWN_PROMPT,
    CREATE_SAMPLE_DATA_PROMPT,
    CREATE_TASK_MULTIMODAL_PROMPT,
    DEBUG_RUN_SELF_SPAWN_TEMPLATE,
    DEBUG_RUN_TEMPLATE,
    GENERATE_DRIVER_PROMPT,
    TEST_EVALUATOR_TEMPLATE,
    get_algorithm_template,
    get_evaluator_template,
    get_multimodal_algorithm_template,
    get_multimodal_evaluator_template,
    get_self_spawn_algorithm_template,
    get_self_spawn_evaluator_template,
)
from llm4ad.infra.provider.base import BaseProvider


class TaskCreator:
    """Generate all LLM4AD application artifacts from analysis."""

    def __init__(self, provider: BaseProvider) -> None:
        """Initialize with an LLM provider for generation calls."""
        self._provider = provider

    async def create(
        self,
        analysis: AnalysisResult,
        description: str,
        *,
        multimodal: bool = False,
    ) -> TaskBlueprint:
        """Generate a complete TaskBlueprint from the analysis.

        Args:
            analysis: Structured problem analysis from TaskAnalyzer.
            description: Original user description for context.
            multimodal: Whether to generate a multimodal evaluator.

        Returns:
            TaskBlueprint with all artifacts populated.
        """
        if analysis.has_evolve_markers:
            return await self._create_reuse_algorithm(analysis, description, multimodal=multimodal)

        evaluator_register_name = analysis.project_name.replace("-", "_") + "_evaluator"
        evaluator_file_name = evaluator_register_name + ".py"
        derived_class_name = self._derive_evaluator_class_name(analysis)

        if multimodal:
            # Multimodal keeps the single dedicated evaluator call (self-spawn +
            # in-process rendering), but algorithm/sample/boilerplate are still
            # generated/rendered separately to relieve truncation pressure.
            algorithm_code, sample_data = await self._generate_multimodal_algorithm_and_data(
                analysis,
                evaluator_register_name,
                derived_class_name,
            )
            evaluator_code = await self._generate_multimodal_evaluator(
                analysis,
                evaluator_register_name,
            )
        else:
            # Split-call flow with a checkpoint after each artifact: generate,
            # validate it against the earliest meaningful check, and repair in
            # place before moving on. The whole-blueprint validate() pass later
            # remains the authoritative gate.
            checkpoint = self._checkpointer()

            algorithm_code = await self._generate_algorithm(analysis)

            # Construct a minimal partial blueprint for checkpoint_algorithm
            partial_blueprint = TaskBlueprint(
                project_name=analysis.project_name,
                task_description=analysis.background,
                evaluator_file_name="",
                evaluator_code="",
                algorithm_code=algorithm_code,
                config_yaml="",
                debug_run_code="",
                evaluator_class_name="",
                algorithm_dir_name=analysis.algorithm_dir_name,
                algorithm_file_name=analysis.algorithm_file_name,
                function_to_evolve=analysis.function_name,
                metrics=analysis.metrics,
                evaluation_pattern=analysis.evaluation_pattern,
            )

            if checkpoint is not None:
                algorithm_code = await checkpoint.checkpoint_algorithm(
                    algorithm_code, blueprint=partial_blueprint, analysis=analysis
                )

            sample_data = await self._generate_sample_data(analysis, algorithm_code)
            if checkpoint is not None:
                sample_data = await checkpoint.checkpoint_sample_data(
                    sample_data,
                    algorithm_code=algorithm_code,
                    function_to_evolve=analysis.function_name,
                    project_name=analysis.project_name,
                )
                algorithm_code, sample_data = await checkpoint.checkpoint_algorithm_trial(
                    algorithm_code=algorithm_code,
                    sample_data=sample_data,
                    algorithm_dir_name=analysis.algorithm_dir_name,
                    algorithm_file_name=analysis.algorithm_file_name,
                    function_to_evolve=analysis.function_name,
                    project_name=analysis.project_name,
                    evaluation_pattern=analysis.evaluation_pattern,
                )

            evaluator_code = await self._generate_evaluator(
                analysis,
                algorithm_code,
                sample_data,
                evaluator_register_name,
                derived_class_name,
            )
            if checkpoint is not None:
                evaluator_code = await checkpoint.checkpoint_evaluator(
                    evaluator_code=evaluator_code,
                    evaluator_file_name=evaluator_file_name,
                    evaluator_class_name=derived_class_name,
                    algorithm_dir_name=analysis.algorithm_dir_name,
                    algorithm_file_name=analysis.algorithm_file_name,
                    algorithm_code=algorithm_code,
                    function_to_evolve=analysis.function_name,
                    metrics=analysis.metrics,
                    evaluation_pattern=analysis.evaluation_pattern,
                )

        # Class name: prefer the AST-extracted name from the real code, fall
        # back to the deterministically derived name we dictated.
        evaluator_class_name_raw = self._extract_evaluator_class_name(evaluator_code)
        evaluator_class_name: str = evaluator_class_name_raw or derived_class_name

        # Deterministic boilerplate.
        debug_run_code = self._render_debug_run(analysis)
        test_evaluator_code = self._render_test_evaluator(
            analysis,
            evaluator_file_name,
            evaluator_class_name,
            multimodal=multimodal,
        )

        dataset_files: dict[str, str] = {}
        if sample_data.strip() and sample_data.strip().upper() != "NONE":
            dataset_files["data/sample/instance_001.json"] = sample_data.strip()

        config_yaml = self._build_config_yaml(
            analysis,
            evaluator_class_name,
            evaluator_file_name,
            multimodal=multimodal,
        )

        return TaskBlueprint(
            project_name=analysis.project_name,
            task_description=analysis.background,
            evaluator_code=evaluator_code,
            algorithm_code=algorithm_code,
            config_yaml=config_yaml,
            debug_run_code=debug_run_code,
            evaluator_class_name=evaluator_class_name,
            evaluator_file_name=evaluator_file_name,
            algorithm_dir_name=analysis.algorithm_dir_name,
            algorithm_file_name=analysis.algorithm_file_name,
            function_to_evolve=analysis.function_name,
            metrics=analysis.metrics,
            evaluation_pattern=analysis.evaluation_pattern,
            dataset_files=dataset_files,
            source_code_path=None,
            test_evaluator_code=test_evaluator_code,
        )

    # ------------------------------------------------------------------
    # Per-artifact checkpointing
    # ------------------------------------------------------------------

    def _checkpointer(self):
        """Build a TaskValidator for in-place per-artifact checkpoints.

        Returns ``None`` when no provider is available (e.g. unit tests that
        construct ``TaskCreator(provider=None)``), so the split-call flow still
        runs without live checkpoint repairs. Imported lazily to avoid a
        circular import (validator imports from this module).
        """
        if self._provider is None:
            return None
        from llm4ad.builder.validator import TaskValidator

        return TaskValidator(self._provider)

    # ------------------------------------------------------------------
    # Split-call generation helpers (one artifact per LLM call)
    # ------------------------------------------------------------------

    async def _generate_algorithm(self, analysis: AnalysisResult) -> str:
        """Generate the algorithm file (single artifact).

        Routes to the correct template and prompt based on evaluation_pattern:
        - separate_script (Variant A): one-shot solver with main() reading argv
        - self_spawn (Variant B): policy function called in an environment loop
        """
        if analysis.evaluation_pattern == "self_spawn":
            prompt = CREATE_ALGORITHM_POLICY_PROMPT.format(
                project_name=analysis.project_name,
                background=analysis.background,
                function_name=analysis.function_name,
                function_signature=analysis.function_signature,
                function_description=analysis.function_description,
                input_format=analysis.input_format,
                output_format=analysis.output_format,
                algorithm_file_name=analysis.algorithm_file_name,
                algorithm_template=get_self_spawn_algorithm_template(),
            )
        else:
            prompt = CREATE_ALGORITHM_PROMPT.format(
                project_name=analysis.project_name,
                background=analysis.background,
                function_name=analysis.function_name,
                function_signature=analysis.function_signature,
                function_description=analysis.function_description,
                input_format=analysis.input_format,
                output_format=analysis.output_format,
                algorithm_file_name=analysis.algorithm_file_name,
                algorithm_template=get_algorithm_template(),
            )
        result = await self._provider.generate(prompt, temperature=0.4, max_tokens=16384)
        return _strip_code_fences(result.text.strip())

    async def _generate_sample_data(
        self,
        analysis: AnalysisResult,
        algorithm_code: str,
    ) -> str:
        """Generate one sample data instance matching the algorithm's input.

        For self_spawn tasks, returns a deterministic seed (no LLM call needed).
        For separate_script tasks, generates via LLM.
        """
        if analysis.evaluation_pattern == "self_spawn":
            return '{"seed": 0}'

        prompt = CREATE_SAMPLE_DATA_PROMPT.format(
            input_format=analysis.input_format,
            algorithm_code=algorithm_code,
        )
        result = await self._provider.generate(prompt, temperature=0.3, max_tokens=16384)
        return _strip_code_fences(result.text.strip())

    async def _generate_evaluator(
        self,
        analysis: AnalysisResult,
        algorithm_code: str,
        sample_data: str,
        evaluator_register_name: str,
        evaluator_class_name: str,
    ) -> str:
        """Generate the evaluator file (single artifact).

        Routes to the correct template and prompt based on evaluation_pattern:
        - separate_script (Variant A): spawns `python algo.py '<instance>'`
        - self_spawn (Variant B): spawns itself, loads policy via importlib
        """
        if analysis.evaluation_pattern == "self_spawn":
            prompt = CREATE_EVALUATOR_SELF_SPAWN_PROMPT.format(
                project_name=analysis.project_name,
                background=analysis.background,
                function_name=analysis.function_name,
                metrics_json=json.dumps(analysis.metrics, indent=2),
                input_format=analysis.input_format,
                output_format=analysis.output_format,
                algorithm_dir_name=analysis.algorithm_dir_name,
                algorithm_file_name=analysis.algorithm_file_name,
                algorithm_code=algorithm_code,
                sample_data=sample_data,
                evaluator_register_name=evaluator_register_name,
                evaluator_class_name=evaluator_class_name,
                evaluator_template=get_self_spawn_evaluator_template(),
            )
        else:
            prompt = CREATE_EVALUATOR_PROMPT.format(
                project_name=analysis.project_name,
                background=analysis.background,
                metrics_json=json.dumps(analysis.metrics, indent=2),
                output_format=analysis.output_format,
                algorithm_dir_name=analysis.algorithm_dir_name,
                algorithm_file_name=analysis.algorithm_file_name,
                algorithm_code=algorithm_code,
                sample_data=sample_data,
                evaluator_register_name=evaluator_register_name,
                evaluator_class_name=evaluator_class_name,
                evaluator_template=get_evaluator_template(),
            )
        result = await self._provider.generate(prompt, temperature=0.4, max_tokens=16384)
        return _strip_code_fences(result.text.strip())

    async def _generate_multimodal_algorithm_and_data(
        self,
        analysis: AnalysisResult,
        evaluator_register_name: str,
        evaluator_class_name: str,
    ) -> tuple[str, str]:
        """Generate the multimodal algorithm file and a matching sample instance."""
        algorithm_code = await self._generate_algorithm(analysis)
        sample_data = await self._generate_sample_data(analysis, algorithm_code)
        return algorithm_code, sample_data

    async def _generate_multimodal_evaluator(
        self,
        analysis: AnalysisResult,
        evaluator_register_name: str,
    ) -> str:
        """Generate the multimodal evaluator via its dedicated single call."""
        viz_spec = analysis.visualization_spec or {}
        prompt = CREATE_TASK_MULTIMODAL_PROMPT.format(
            project_name=analysis.project_name,
            background=analysis.background,
            function_name=analysis.function_name,
            function_signature=analysis.function_signature,
            function_description=analysis.function_description,
            input_format=analysis.input_format,
            output_format=analysis.output_format,
            metrics_json=json.dumps(analysis.metrics, indent=2),
            algorithm_dir_name=analysis.algorithm_dir_name,
            algorithm_file_name=analysis.algorithm_file_name,
            evaluator_register_name=evaluator_register_name,
            evaluator_template=get_multimodal_evaluator_template(),
            algorithm_template=get_multimodal_algorithm_template(),
            visualization_spec_json=json.dumps(viz_spec, indent=2),
        )
        result = await self._provider.generate(prompt, temperature=0.4, max_tokens=16384)
        sections = self._parse_sections(result.text)
        return sections.get("EVALUATOR_CODE", _strip_code_fences(result.text))

    # ------------------------------------------------------------------
    # Deterministic boilerplate rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_evaluator_class_name(analysis: AnalysisResult) -> str:
        """Derive a PascalCase evaluator class name from the project name."""
        slug = analysis.project_name.replace("-", "_")
        parts = [p for p in slug.split("_") if p]
        pascal = "".join(word[:1].upper() + word[1:] for word in parts)
        return (pascal or "Task") + "Evaluator"

    @staticmethod
    def _render_debug_run(analysis: AnalysisResult) -> str:
        """Render debug_run.py deterministically."""
        if analysis.evaluation_pattern == "self_spawn":
            return DEBUG_RUN_SELF_SPAWN_TEMPLATE.format(
                algorithm_dir_name=analysis.algorithm_dir_name,
                algorithm_file_name=analysis.algorithm_file_name,
                function_name=analysis.function_name,
            )
        return DEBUG_RUN_TEMPLATE.format(
            algorithm_dir_name=analysis.algorithm_dir_name,
            algorithm_file_name=analysis.algorithm_file_name,
        )

    @staticmethod
    def _render_test_evaluator(
        analysis: AnalysisResult,
        evaluator_file_name: str,
        evaluator_class_name: str,
        *,
        multimodal: bool = False,
    ) -> str:
        """Render test_evaluator.py deterministically."""
        expected_metrics = [m["name"] for m in analysis.metrics]
        behavior_kwarg = 'behavior_storage="rendered",' if multimodal else ""
        return TEST_EVALUATOR_TEMPLATE.format(
            evaluator_module_name=evaluator_file_name.removesuffix(".py"),
            evaluator_class_name=evaluator_class_name,
            expected_metrics_list=json.dumps(expected_metrics),
            behavior_storage_kwarg=behavior_kwarg,
        )

    # ------------------------------------------------------------------
    # Reuse algorithm path (from_code with EVOLVE markers)
    # ------------------------------------------------------------------

    async def _create_reuse_algorithm(
        self,
        analysis: AnalysisResult,
        description: str,
        *,
        multimodal: bool = False,
    ) -> TaskBlueprint:
        """Generate artifacts using the EVOLVE block, branching on its semantic role.

        For complete_solver: programmatically assemble algorithm file.
        For sub_function/helper: LLM generates a driver script that wires the EVOLVE function.
        Then LLM generates evaluator + supporting files based on the algorithm/driver.
        """
        evaluator_register_name = analysis.project_name.replace("-", "_") + "_evaluator"
        metrics_json = json.dumps(analysis.metrics, indent=2)
        evaluator_template = (
            get_multimodal_evaluator_template() if multimodal else get_evaluator_template()
        )

        # Branch on function role
        if analysis.function_role == "complete_solver":
            algorithm_code = self._assemble_algorithm_file(analysis)
        else:
            # sub_function or helper: generate driver via LLM
            algorithm_code = await self._generate_driver_via_llm(analysis, description)

        # Generate supporting artifacts using the algorithm/driver as contract.
        evaluator_file_name = evaluator_register_name + ".py"
        derived_class_name = self._derive_evaluator_class_name(analysis)

        # Sample data first (grounded in the assembled algorithm/driver), then
        # the evaluator (grounded in both algorithm and sample data). Each is
        # checkpointed in place before the next step. The algorithm itself is
        # not repaired here in reuse mode: for user-provided code we must not
        # rewrite it, and the assembled/driver code is derived from it.
        checkpoint = None if multimodal else self._checkpointer()

        sample_data = await self._generate_sample_data(analysis, algorithm_code)
        if checkpoint is not None:
            sample_data = await checkpoint.checkpoint_sample_data(
                sample_data,
                algorithm_code=algorithm_code,
                function_to_evolve=analysis.function_name,
                project_name=analysis.project_name,
            )
            # No algorithm_trial checkpoint in reuse mode: the algorithm file
            # embeds user-provided EVOLVE code that must not be rewritten. The
            # final validate() pass still runs the trial (and skips algorithm
            # repair via its reuse_algorithm guard).

        if multimodal:
            evaluator_code = await self._generate_multimodal_evaluator(
                analysis,
                evaluator_register_name,
            )
        else:
            input_schema_json = json.dumps(analysis.input_schema or {}, indent=2)
            output_schema_json = json.dumps(analysis.output_schema or {}, indent=2)
            prompt = CREATE_EVALUATOR_FROM_DRIVER_PROMPT.format(
                project_name=analysis.project_name,
                background=analysis.background,
                function_name=analysis.function_name,
                metrics_json=metrics_json,
                algorithm_dir_name=analysis.algorithm_dir_name,
                algorithm_file_name=analysis.algorithm_file_name,
                driver_code=algorithm_code,
                input_schema_json=input_schema_json,
                output_schema_json=output_schema_json,
                sample_data=sample_data,
                evaluator_register_name=evaluator_register_name,
                evaluator_class_name=derived_class_name,
                evaluator_template=evaluator_template,
            )
            result = await self._provider.generate(prompt, temperature=0.4, max_tokens=16384)
            evaluator_code = _strip_code_fences(result.text.strip())
            if checkpoint is not None:
                evaluator_code = await checkpoint.checkpoint_evaluator(
                    evaluator_code=evaluator_code,
                    evaluator_file_name=evaluator_file_name,
                    evaluator_class_name=derived_class_name,
                    algorithm_dir_name=analysis.algorithm_dir_name,
                    algorithm_file_name=analysis.algorithm_file_name,
                    algorithm_code=algorithm_code,
                    function_to_evolve=analysis.function_name,
                    metrics=analysis.metrics,
                    evaluation_pattern=analysis.evaluation_pattern,
                )

        evaluator_class_name_raw = self._extract_evaluator_class_name(evaluator_code)
        evaluator_class_name = evaluator_class_name_raw or derived_class_name

        debug_run_code = self._render_debug_run(analysis)
        test_evaluator_code = self._render_test_evaluator(
            analysis,
            evaluator_file_name,
            evaluator_class_name,
            multimodal=multimodal,
        )

        dataset_files: dict[str, str] = {}
        if sample_data.strip() and sample_data.strip().upper() != "NONE":
            dataset_files["data/sample/instance_001.json"] = sample_data.strip()

        config_yaml = self._build_config_yaml(
            analysis,
            evaluator_class_name,
            evaluator_file_name,
            multimodal=multimodal,
            algorithm_code_override=algorithm_code,
        )

        return TaskBlueprint(
            project_name=analysis.project_name,
            task_description=analysis.background,
            evaluator_code=evaluator_code,
            algorithm_code=algorithm_code,
            config_yaml=config_yaml,
            debug_run_code=debug_run_code,
            evaluator_class_name=evaluator_class_name,
            evaluator_file_name=evaluator_file_name,
            algorithm_dir_name=analysis.algorithm_dir_name,
            algorithm_file_name=analysis.algorithm_file_name,
            function_to_evolve=analysis.function_name,
            metrics=analysis.metrics,
            evaluation_pattern=analysis.evaluation_pattern,
            dataset_files=dataset_files,
            source_code_path=None,
            test_evaluator_code=test_evaluator_code,
        )

    async def _generate_driver_via_llm(
        self,
        analysis: AnalysisResult,
        description: str,
    ) -> str:
        """Generate a driver script via LLM for sub_function/helper roles."""
        classifier_output = json.dumps(
            {
                "function_role": analysis.function_role,
                "input_schema": analysis.input_schema,
                "output_schema": analysis.output_schema,
                "needed_helpers": analysis.needed_helpers,
                "driver_strategy": analysis.driver_strategy,
            },
            indent=2,
        )

        prompt = GENERATE_DRIVER_PROMPT.format(
            description=description,
            full_code=analysis.algorithm_full_code or "",
            evolve_block=analysis.evolve_block_content or "",
            classifier_output=classifier_output,
        )

        result = await self._provider.generate(prompt, temperature=0.3, max_tokens=16384)
        return result.text.strip()

    @staticmethod
    def _assemble_algorithm_file(analysis: AnalysisResult) -> str:
        """Programmatically assemble a standalone algorithm file from the EVOLVE block.

        For complete_solver role: wraps the EVOLVE function with solve()/main() boilerplate.
        Uses input_schema to determine how to call the function.
        """
        evolve_content = analysis.evolve_block_content or ""
        func_name = analysis.function_name
        input_schema = analysis.input_schema or {}

        # Separate import lines from the rest of the EVOLVE block
        import_lines: list[str] = []
        code_lines: list[str] = []
        for line in evolve_content.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and "EVOLVE" not in stripped:
                import_lines.append(line)
            else:
                code_lines.append(line)

        evolve_body = "\n".join(code_lines)

        # Auto-detect commonly used modules referenced in the EVOLVE block
        auto_imports: list[str] = []
        all_evolve_text = evolve_content
        existing_import_text = "\n".join(import_lines)

        module_patterns = [
            ("np.", "numpy", "import numpy as np"),
            ("np.ndarray", "numpy", "import numpy as np"),
            ("math.", "math", "import math"),
            ("heapq.", "heapq", "import heapq"),
            ("heapq.heappush", "heapq", "import heapq"),
            ("copy.", "copy", "import copy"),
            ("copy.deepcopy", "copy", "import copy"),
            ("random.", "random", "import random"),
            ("itertools.", "itertools", "import itertools"),
            ("collections.", "collections", "from collections import deque"),
            ("deque(", "collections", "from collections import deque"),
            ("defaultdict(", "collections", "from collections import defaultdict"),
        ]
        for pattern, _module, import_stmt in module_patterns:
            if pattern in all_evolve_text and import_stmt not in existing_import_text:
                auto_imports.append(import_stmt)
                existing_import_text += f"\n{import_stmt}"

        # Combine all imports (deduplicated)
        all_imports = list(dict.fromkeys(import_lines + auto_imports))
        extra_imports = "\n".join(all_imports)
        if extra_imports:
            extra_imports = extra_imports + "\n"

        # Generate solve() body based on input_schema
        # If schema has a single key, pass that value directly; otherwise use **input_data
        if len(input_schema) == 1:
            single_key = list(input_schema.keys())[0]
            solve_call = f'result = {func_name}(input_data["{single_key}"])'
        else:
            solve_call = f"result = {func_name}(**input_data)"

        return (
            "#!/usr/bin/env python3\n"
            f'"""Standalone algorithm file for LLM4AD evolution.\n'
            f"\n"
            f"Function to evolve: {func_name}\n"
            f'"""\n'
            "\n"
            "import json\n"
            "import sys\n"
            f"{extra_imports}"
            "\n"
            "# EVOLVE_START\n"
            f"{evolve_body}\n"
            "# EVOLVE_END\n"
            "\n"
            "\n"
            f"def solve(input_data):\n"
            f'    """Main solving function that delegates to the evolved algorithm."""\n'
            f"    {solve_call}\n"
            f"    if isinstance(result, dict):\n"
            f"        return result\n"
            f'    return {{"result": result}}\n'
            "\n"
            "\n"
            "def main():\n"
            '    """Entry point: read JSON from sys.argv[1], run algorithm, print JSON result."""\n'
            "    if len(sys.argv) < 2:\n"
            "        print(\"Usage: python solve.py '<input_json>'\")\n"
            "        sys.exit(1)\n"
            "\n"
            "    input_data = json.loads(sys.argv[1])\n"
            "    result = solve(input_data)\n"
            "    print(json.dumps(result))\n"
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
        )

    # ------------------------------------------------------------------
    # Config generation (programmatic)
    # ------------------------------------------------------------------

    def _build_config_yaml(
        self,
        analysis: AnalysisResult,
        evaluator_class_name: str,
        evaluator_file_name: str,
        *,
        multimodal: bool = False,
        algorithm_code_override: str | None = None,
        evolution_yaml: str | None = None,
    ) -> str:
        """Build the YAML config programmatically from analysis results.

        Args:
            analysis: Structured problem analysis.
            evaluator_class_name: Name of the generated evaluator class.
            evaluator_file_name: Filename of the generated evaluator module.
            multimodal: Whether to include multimodal config section.
            algorithm_code_override: Custom algorithm code for the prompt template.
            evolution_yaml: Pre-rendered evolution YAML block. When ``None``,
                the default (simple-tier) parameters are used.
        """
        # Indent background for YAML block scalar
        background_indented = textwrap.indent(analysis.background.strip(), "  ")

        # Evaluator module reference
        evaluator_module = f"{evaluator_file_name}:{evaluator_class_name}"

        # Metrics list for YAML
        metric_names = [m["name"] for m in analysis.metrics]
        metrics_list = json.dumps(metric_names)

        # Dataset YAML
        if analysis.dataset_summary:
            dataset_yaml = '    mode: "directory"\n    path: "data/sample"\n    recursive: false'
        else:
            dataset_yaml = '    mode: "directory"\n    path: "data/sample"\n    recursive: false'

        # Multimodal config section
        if multimodal:
            multimodal_config_yaml = (
                "# ===== Multimodal Configuration =====\n"
                "multimodal:\n"
                "  enabled: true\n"
                "  max_images_per_prompt: 3\n"
                "  image_max_size_kb: 512\n"
                "  include_observation_text: true\n"
                '  behavior_storage: "rendered"\n'
                "\n"
            )
            planner_samplers_yaml = (
                '    - name: "multimodal_mutation_sampler"\n'
                '    - name: "multimodal_crossover_sampler"'
            )
        else:
            multimodal_config_yaml = ""
            planner_samplers_yaml = (
                '    - name: "mutation_sampler"\n' '    - name: "crossover_sampler"'
            )

        # Evolution parameters (rule-based or default)
        evo_yaml = evolution_yaml if evolution_yaml is not None else render_default_evolution_yaml()

        # Build coder prompt_template
        prompt_template = self._build_coder_prompt(
            analysis,
            algorithm_code_override=algorithm_code_override,
        )
        prompt_template_indented = textwrap.indent(prompt_template, "    ")

        config = CONFIG_YAML_TEMPLATE.format(
            project_name=analysis.project_name,
            background_indented=background_indented,
            multimodal_config_yaml=multimodal_config_yaml,
            evaluator_module=evaluator_module,
            metrics_list=metrics_list,
            dataset_yaml=dataset_yaml,
            algorithm_dir_name=analysis.algorithm_dir_name,
            planner_samplers_yaml=planner_samplers_yaml,
            prompt_template_indented=prompt_template_indented,
            evolution_yaml=evo_yaml,
        )
        return config

    def _build_coder_prompt(
        self,
        analysis: AnalysisResult,
        *,
        algorithm_code_override: str | None = None,
    ) -> str:
        """Build the coder prompt_template field for the YAML config.

        Args:
            analysis: Structured problem analysis.
            algorithm_code_override: When set, use this as the algorithm code
                in the prompt instead of generating a placeholder.
        """
        # Build optimization goals from metrics
        goals = []
        for m in analysis.metrics:
            direction = "higher is better" if m.get("type") == "maximize" else "lower is better"
            goals.append(f"- {m['name']}: {m.get('description', '')} ({direction})")
        optimization_goals = "\n".join(goals)

        if algorithm_code_override is not None:
            algorithm_code_for_prompt = algorithm_code_override
        else:
            # Build a placeholder algorithm code block for the prompt
            algorithm_code_for_prompt = (
                f"import json\nimport sys\n\n"
                f"# EVOLVE_START\n"
                f"{analysis.function_signature}\n"
                f'    """{analysis.function_description}"""\n'
                f"    pass\n"
                f"# EVOLVE_END\n\n"
                f"def process(data):\n"
                f"    result = {analysis.function_name}(data)\n"
                f'    return {{"result": result}}\n\n'
                f"def main():\n"
                f"    if len(sys.argv) < 2:\n"
                f"        sys.exit(1)\n"
                f"    input_data = json.loads(sys.argv[1])\n"
                f"    result = process(input_data)\n"
                f"    print(json.dumps(result))\n\n"
                f'if __name__ == "__main__":\n'
                f"    main()"
            )

        return CODER_PROMPT_TEMPLATE.format(
            task_description=analysis.project_name.replace("_", " "),
            background=analysis.background,
            function_name=analysis.function_name,
            algorithm_code_for_prompt=algorithm_code_for_prompt,
            input_format=analysis.input_format,
            output_format=analysis.output_format,
            optimization_goals=optimization_goals,
        )

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_sections(text: str) -> dict[str, str]:
        """Parse delimited sections from LLM response.

        Expects format: ===SECTION_NAME=== followed by content.
        """
        sections: dict[str, str] = {}
        pattern = r"===([A-Z_]+)==="
        parts = re.split(pattern, text)

        # parts[0] is before first delimiter, then alternating name/content
        i = 1
        while i < len(parts) - 1:
            name = parts[i].strip()
            content = parts[i + 1].strip()
            # Strip leading/trailing markdown code fences
            content = _strip_code_fences(content)
            sections[name] = content
            i += 2

        if not sections:
            logger.warning(
                "No delimited sections found in response, treating as single evaluator block"
            )
            sections["EVALUATOR_CODE"] = _strip_code_fences(text)

        return sections

    @staticmethod
    def _extract_evaluator_class_name(evaluator_code: str) -> str | None:
        """Extract the evaluator class name from generated code using AST.

        Prefers the class decorated with ``@BaseEvaluator.register(...)``, then
        falls back to any class whose base is ``BaseEvaluator`` (handling both
        ``BaseEvaluator`` and ``<module>.BaseEvaluator`` forms). This keeps the
        config's ``module`` reference aligned with whatever name the LLM chose.
        """
        try:
            tree = ast.parse(evaluator_code)
        except SyntaxError:
            return None

        def _is_base_evaluator(base: ast.expr) -> bool:
            if isinstance(base, ast.Name):
                return base.id == "BaseEvaluator"
            if isinstance(base, ast.Attribute):
                return base.attr == "BaseEvaluator"
            return False

        # First pass: a class carrying @BaseEvaluator.register(...).
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for dec in node.decorator_list:
                    if (
                        isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == "register"
                    ):
                        return node.name

        # Second pass: any class directly inheriting from BaseEvaluator.
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                _is_base_evaluator(base) for base in node.bases
            ):
                return node.name
        return None


def _strip_code_fences(text: str) -> str:
    r"""Remove markdown code fences and any surrounding prose.

    Common LLM output patterns:
      1. Prose on both sides: "Here is the code:\n```python\n...\n```\nHope this helps."
      2. Only opening fence, no closing fence (truncated by max_tokens).
      3. Multiple fence blocks (rare; we take the first one).

    Take the content between the first fence and the next fence (or EOF).
    Any leftover ``` lines (nested or extra) are stripped defensively to
    avoid leaving fence markers inside the returned code.
    """
    text = text.strip()
    fence_start = text.find("```")
    if fence_start == -1:
        return text

    after_fence = text[fence_start:]
    lines = after_fence.split("\n")
    # Drop the opening ```python / ``` line.
    lines = lines[1:]
    # Cut at the next standalone ``` (closing fence) and discard trailing prose.
    for idx, line in enumerate(lines):
        if line.strip() == "```":
            lines = lines[:idx]
            break
    cleaned = "\n".join(lines).strip()
    # Defensive: drop any remaining bare ``` lines so they don't cause SyntaxError.
    cleaned_lines = [
        line
        for line in cleaned.split("\n")
        if line.strip() != "```" and not line.strip().startswith("```")
    ]
    return "\n".join(cleaned_lines).strip()
