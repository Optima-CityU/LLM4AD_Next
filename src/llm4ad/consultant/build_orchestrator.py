"""Build orchestrator for the merged chat command.

Wraps the builder pipeline (TaskAnalyzer → TaskCreator → TaskValidator)
for use within the interactive chat session. Provides progress reporting
and graceful error handling with fallback to re-gathering needs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from llm4ad.builder.analyzer import TaskAnalyzer
from llm4ad.builder.blueprint import AnalysisResult, TaskBlueprint
from llm4ad.builder.config_recommender import ConfigRecommender, render_evolution_yaml
from llm4ad.builder.creator import TaskCreator
from llm4ad.builder.prompts import REQUIREMENTS_PROMPT
from llm4ad.builder.validator import TaskValidator
from llm4ad.builder.writer import write_task_directory
from llm4ad.consultant.needs import NeedsProfile
from llm4ad.infra.provider.base import BaseProvider, ChatMessage


class BuildError(Exception):
    """Raised when the build pipeline fails.

    Carries the partially-built blueprint (when available) so callers that want
    to inspect or repair the failed artifacts can access them. The interactive
    ``chat`` path ignores this attribute and behaves exactly as before; the
    agent build path uses it to persist the failed package to the workspace so
    the agent can fix it in place.

    Attributes:
        blueprint: The last blueprint produced before the failure, or ``None``
            if the failure occurred before any blueprint was created.
    """

    def __init__(self, message: str, blueprint: TaskBlueprint | None = None) -> None:
        """Initialize the error.

        Args:
            message: Human-readable failure description.
            blueprint: The partially-built blueprint, if one exists.
        """
        super().__init__(message)
        self.blueprint = blueprint


class BuildOrchestrator:
    """Orchestrates the builder pipeline within the chat session.

    Accepts a NeedsProfile from Phase 1 and produces a validated
    TaskBlueprint, reporting progress to the user via Rich console.
    """

    def __init__(
        self,
        provider: BaseProvider,
        console: Console | None = None,
        max_repair_attempts: int = 10,
    ) -> None:
        """Initialize the orchestrator.

        Args:
            provider: LLM provider for builder agents.
            console: Rich console for progress display.
            max_repair_attempts: Maximum auto-repair attempts during validation.
        """
        self._provider = provider
        self._console = console or Console()
        self._max_repair = max_repair_attempts

    async def build(self, needs: NeedsProfile) -> TaskBlueprint:
        """Run the full build pipeline from a NeedsProfile.

        When use_existing_evaluator=true and code_path is provided, uses
        "lightweight integration mode": directly uses existing code structure
        instead of regenerating everything.

        Args:
            needs: Structured needs from Phase 1 conversation.

        Returns:
            Validated TaskBlueprint ready for review.

        Raises:
            BuildError: If the pipeline fails after all repair attempts.
        """
        # Check if we should use existing-project integration mode
        if needs.use_existing_evaluator and needs.code_path and needs.existing_evaluator_path:
            return await self._build_from_existing_project(needs)

        # Otherwise, use standard full generation mode
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self._console,
            transient=True,
        ) as progress:
            task = progress.add_task("Analyzing problem...", total=6)

            # Stage 1: Analyze
            analysis = await self._analyze(needs)
            progress.update(task, advance=1, description="Generating artifacts...")

            # Stage 2: Create
            blueprint = await self._create(analysis, needs)
            progress.update(task, advance=1, description="Validating code...")

            # Stage 3: Validate & repair
            blueprint = await self._validate(blueprint)
            progress.update(task, advance=1, description="Generating requirements...")

            # Stage 4: Generate requirements.txt (LLM pass)
            blueprint = await self._generate_requirements(blueprint, analysis)
            progress.update(task, advance=1, description="Configuring parameters...")

            # Stage 5: Configure (rule-based parameter recommendation)
            blueprint = self._configure(blueprint, analysis)
            progress.update(task, advance=1, description="Build complete.")

        if not blueprint.is_valid():
            errors_summary = "\n".join(f"  - {e}" for e in blueprint.validation_errors)
            raise BuildError(
                f"Validation failed after {blueprint.repair_attempts} attempts:\n"
                f"{errors_summary}",
                blueprint=blueprint,
            )

        return blueprint

    async def _build_from_existing_project(self, needs: NeedsProfile) -> TaskBlueprint:
        """Build by reusing existing project files, generating only what's missing.

        Scans the user-provided project directory for all standard artifacts:
        evaluator, algorithm, config, debug_run, test_evaluator, dataset.
        Uses whatever exists as-is. Generates ONLY missing pieces via LLM.
        Always runs validation at the end.

        Args:
            needs: Needs profile with existing_evaluator_path and code_path set.

        Returns:
            Validated TaskBlueprint.

        Raises:
            BuildError: If validation fails after repair attempts.
        """
        assert needs.code_path is not None
        assert needs.existing_evaluator_path is not None

        code_path = Path(needs.code_path).expanduser().resolve()
        evaluator_path = Path(needs.existing_evaluator_path).expanduser().resolve()

        self._console.print(
            "[bold blue]Integrating existing project...[/bold blue]"
        )

        # --- Step 1: Read all existing files ---

        # Evaluator (required - already confirmed to exist)
        try:
            evaluator_code = evaluator_path.read_text(encoding="utf-8")
        except Exception as e:
            raise BuildError(f"Failed to read evaluator: {e}") from e

        evaluator_class_name = self._extract_evaluator_class_name(evaluator_code)
        evaluator_file_name = evaluator_path.name
        self._console.print(
            f"  [green]✓[/green] Evaluator: {evaluator_file_name} "
            f"(class: {evaluator_class_name})"
        )

        # Algorithm
        algorithm_code, algo_dir_name, algo_file_name, function_name = (
            self._scan_algorithm(code_path)
        )
        if algorithm_code:
            self._console.print(
                f"  [green]✓[/green] Algorithm: {algo_dir_name}/{algo_file_name}"
            )
        else:
            self._console.print("  [yellow]○[/yellow] Algorithm: not found, will generate")

        # Config
        config_yaml = self._read_existing_config(code_path)
        if config_yaml:
            self._console.print("  [green]✓[/green] Config: config.yaml")
        else:
            self._console.print("  [yellow]○[/yellow] Config: not found, will generate")

        # debug_run.py
        debug_run_code = self._read_file_if_exists(code_path / "debug_run.py")
        if debug_run_code:
            debug_run_code = self._normalize_config_reference(debug_run_code)
            self._console.print("  [green]✓[/green] Debug runner: debug_run.py")

        # test_evaluator.py
        test_evaluator_code = self._read_file_if_exists(code_path / "test_evaluator.py")
        if test_evaluator_code:
            self._console.print("  [green]✓[/green] Test evaluator: test_evaluator.py")

        # Dataset files
        dataset_files = self._scan_dataset_files(code_path, needs.data_path, config_yaml)
        if dataset_files:
            self._console.print(
                f"  [green]✓[/green] Dataset: {len(dataset_files)} sample file(s)"
            )

        # Project name from config or directory name
        project_name = needs.project_name or code_path.name

        # --- Step 2: Generate ONLY missing artifacts ---

        generated_any = False

        if not algorithm_code:
            generated_any = True
            self._console.print("[dim]  Generating algorithm code...[/dim]")
            analysis = await self._analyze(needs)
            creator = TaskCreator(self._provider)
            tmp_bp = await creator.create(analysis, needs.description)
            algorithm_code = tmp_bp.algorithm_code
            algo_dir_name = tmp_bp.algorithm_dir_name
            algo_file_name = tmp_bp.algorithm_file_name
            function_name = tmp_bp.function_to_evolve
            if not config_yaml:
                config_yaml = tmp_bp.config_yaml
            if not debug_run_code:
                debug_run_code = tmp_bp.debug_run_code
            if not test_evaluator_code:
                test_evaluator_code = tmp_bp.test_evaluator_code
            if not dataset_files:
                dataset_files = tmp_bp.dataset_files

        if not config_yaml:
            generated_any = True
            self._console.print("[dim]  Generating config...[/dim]")
            config_yaml = self._generate_minimal_config(
                project_name=project_name,
                evaluator_file_name=evaluator_file_name,
                evaluator_class_name=evaluator_class_name,
                algo_dir_name=algo_dir_name,
                algo_file_name=algo_file_name,
                data_path=needs.data_path,
            )

        if not debug_run_code:
            generated_any = True
            debug_run_code = self._generate_debug_run(project_name)

        if not test_evaluator_code:
            generated_any = True
            test_evaluator_code = self._generate_test_evaluator(
                evaluator_file_name, evaluator_class_name
            )

        if not dataset_files:
            generated_any = True
            dataset_files = {"data/sample/placeholder.json": '{"note": "placeholder"}'}

        if generated_any:
            self._console.print("[dim]  Gap-filling complete.[/dim]")

        # --- Step 3: Assemble blueprint ---

        blueprint = TaskBlueprint(
            project_name=project_name,
            task_description=needs.description,
            evaluator_code=evaluator_code,
            algorithm_code=algorithm_code,
            config_yaml=config_yaml,
            debug_run_code=debug_run_code,
            evaluator_class_name=evaluator_class_name,
            evaluator_file_name=evaluator_file_name,
            algorithm_dir_name=algo_dir_name,
            algorithm_file_name=algo_file_name,
            function_to_evolve=function_name,
            metrics=self._extract_metrics_from_evaluator(evaluator_code),
            dataset_files=dataset_files,
            test_evaluator_code=test_evaluator_code,
            source_code_path=str(code_path),
            reuse_algorithm=True,
        )

        # --- Step 4: Validate ---
        # Skip debug_run stage for existing projects — it would try to run
        # the full pipeline which needs real environment (API keys, full data).
        self._console.print("[dim]  Validating assembled project...[/dim]")
        blueprint = await self._validate(blueprint, skip_debug_run=True)

        if not blueprint.is_valid():
            errors_summary = "\n".join(f"  - {e}" for e in blueprint.validation_errors)
            raise BuildError(
                f"Validation failed after {blueprint.repair_attempts} attempts:\n"
                f"{errors_summary}",
                blueprint=blueprint,
            )

        self._console.print("[bold green]  ✓ Project validated successfully.[/bold green]")
        return blueprint

    # ------------------------------------------------------------------
    # Helpers for existing-project mode
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_evaluator_class_name(evaluator_code: str) -> str:
        """Extract the evaluator class name from source code.

        Args:
            evaluator_code: Python source of the evaluator file.

        Returns:
            Class name string, or "TaskEvaluator" as fallback.
        """
        match = re.search(
            r"class\s+(\w+)\s*\(.*(?:Evaluator|BaseEvaluator).*\)",
            evaluator_code,
        )
        return match.group(1) if match else "TaskEvaluator"

    @staticmethod
    def _scan_algorithm(code_path: Path) -> tuple[str, str, str, str]:
        """Find and read the algorithm file from an existing project.

        Looks for Python files with EVOLVE markers first, then falls back
        to common naming patterns. Skips generated output directories.

        Args:
            code_path: Root of the existing project.

        Returns:
            Tuple of (code, dir_name, file_name, function_name).
            All empty strings if nothing found.
        """
        skip_names = {
            ".git", "__pycache__", ".pytest_cache", "node_modules",
            "runs", "worktrees", "checkpoints", "generated", "temp",
            "best", "state", "logs",
        }

        def _should_skip(py_file: Path) -> bool:
            """Check if a file is in a directory that should be skipped."""
            rel_parts = py_file.relative_to(code_path).parts
            for part in rel_parts:
                if part in skip_names:
                    return True
                if part.startswith("island_"):
                    return True
            return False

        # First pass: look for EVOLVE markers in any .py file in subdirectories
        for py_file in code_path.rglob("*.py"):
            if _should_skip(py_file):
                continue
            if py_file.parent == code_path:
                continue  # skip top-level files, algorithm is usually in subdir
            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue
            if "EVOLVE_START" in content and "EVOLVE_END" in content:
                func_match = re.search(
                    r"#\s*EVOLVE_START.*\ndef\s+(\w+)", content, re.DOTALL
                )
                func_name = func_match.group(1) if func_match else "solve"
                return content, py_file.parent.name, py_file.name, func_name

        # Second pass: common patterns (prefer *_algorithm dirs)
        for pattern in ["*_algorithm/*.py", "*/solve.py", "*/algorithm.py"]:
            for py_file in code_path.glob(pattern):
                if _should_skip(py_file):
                    continue
                try:
                    content = py_file.read_text(encoding="utf-8")
                except Exception:
                    continue
                func_match = re.search(r"def\s+(\w+)", content)
                func_name = func_match.group(1) if func_match else "solve"
                return content, py_file.parent.name, py_file.name, func_name

        return "", "", "", ""

    @staticmethod
    def _read_existing_config(code_path: Path) -> str:
        """Read the project config YAML from the project directory.

        Looks for config.yaml first, then falls back to any YAML file
        containing 'project_name' (a required field in LLM4AD configs).

        Args:
            code_path: Root of the existing project.

        Returns:
            Config content string or empty string.
        """
        config_path = code_path / "config.yaml"
        if config_path.exists():
            try:
                return config_path.read_text(encoding="utf-8")
            except Exception:
                pass

        for yaml_file in sorted(code_path.glob("*.yaml")):
            if yaml_file.name == "config.yaml":
                continue
            try:
                content = yaml_file.read_text(encoding="utf-8")
                if "project_name" in content and "evaluator" in content:
                    return content
            except Exception:
                continue
        return ""

    @staticmethod
    def _read_file_if_exists(path: Path) -> str:
        """Read a file if it exists, return empty string otherwise."""
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except Exception:
                pass
        return ""

    @staticmethod
    def _normalize_config_reference(debug_run_code: str) -> str:
        """Normalize config filename references in debug_run.py to 'config.yaml'.

        The validator always writes config as config.yaml in the temp directory.
        Existing debug_run.py may reference a different config filename (e.g.,
        'tsp_benchmark.yaml'). This ensures consistency during validation.

        Args:
            debug_run_code: Source code of debug_run.py.

        Returns:
            Modified source with config references normalized.
        """
        return re.sub(
            r'LLM4AD\(\s*["\']([^"\']+\.ya?ml)["\']\s*\)',
            'LLM4AD("config.yaml")',
            debug_run_code,
        )

    @staticmethod
    def _scan_dataset_files(
        code_path: Path, data_path: str | None, config_yaml: str = ""
    ) -> dict[str, str]:
        """Scan for dataset files, preserving relative directory structure.

        Determines the dataset directory from (in priority order):
        1. The config YAML's evaluator.dataset.path field
        2. The user-provided data_path
        3. Default: code_path / "data"

        Files are keyed by their path relative to the project root so the
        validator's temp directory mirrors the real project layout.

        Args:
            code_path: Root of the existing project.
            data_path: Optional explicit data directory path from user.
            config_yaml: Optional config content to extract dataset path.

        Returns:
            Dict mapping relative paths to file contents. Limited to 5 files.
        """
        # Try to extract dataset path from config
        config_dataset_path = ""
        if config_yaml:
            import yaml
            try:
                cfg = yaml.safe_load(config_yaml)
                ds_cfg = cfg.get("evaluator", {}).get("dataset", {})
                config_dataset_path = ds_cfg.get("path", "")
            except Exception:
                pass

        # Determine the target data directory
        if config_dataset_path:
            data_dir = code_path / config_dataset_path
        elif data_path:
            data_dir = Path(data_path)
        else:
            data_dir = code_path / "data"

        if not data_dir.is_dir():
            # Fall back to code_path/data if the specific subdir doesn't exist
            data_dir = code_path / "data"
            if not data_dir.is_dir():
                return {}

        result: dict[str, str] = {}
        for json_file in sorted(data_dir.rglob("*.json"))[:5]:
            try:
                content = json_file.read_text(encoding="utf-8")
                try:
                    rel_path = str(json_file.relative_to(code_path)).replace("\\", "/")
                except ValueError:
                    rel_path = f"data/{json_file.name}"
                result[rel_path] = content
            except Exception:
                continue
        return result

    @staticmethod
    def _generate_minimal_config(
        project_name: str,
        evaluator_file_name: str,
        evaluator_class_name: str,
        algo_dir_name: str,
        algo_file_name: str,
        data_path: str | None = None,
    ) -> str:
        """Generate a minimal valid config YAML when none exists.

        Args:
            project_name: Project name slug.
            evaluator_file_name: Evaluator Python file name.
            evaluator_class_name: Evaluator class name.
            algo_dir_name: Algorithm directory name.
            algo_file_name: Algorithm file name.
            data_path: Optional path to dataset.

        Returns:
            YAML string.
        """
        module_ref = f"{evaluator_file_name}:{evaluator_class_name}"
        lines = [
            f"project_name: \"{project_name}\"",
            f"background: \"Algorithm design for {project_name}\"",
            "random_seed: 42",
            "base_dir: \"./runs\"",
            "",
            "providers:",
            "  - name: \"default\"",
            "    type: \"openai_compatible\"",
            "",
            "evaluator:",
            f"  module: \"{module_ref}\"",
            "  timeout: 60.0",
        ]

        if data_path:
            lines.extend([
                "  dataset:",
                "    mode: directory",
                f"    path: \"{data_path}\"",
            ])

        lines.extend([
            "",
            "evolution:",
            "  population_size: 10",
            "  generations: 20",
            "",
            "coder:",
            "  function_name: \"solve\"",
            f"  source_dir: \"{algo_dir_name}\"",
            f"  source_file: \"{algo_file_name}\"",
            "",
            "planner:",
            "  provider_name: \"default\"",
        ])
        return "\n".join(lines)

    @staticmethod
    def _generate_debug_run(project_name: str) -> str:
        """Generate a minimal debug_run.py script."""
        return (
            '"""Debug entry point for the pipeline."""\n'
            "\n"
            "import asyncio\n"
            "import os\n"
            "from pathlib import Path\n"
            "\n"
            "from llm4ad import LLM4AD\n"
            "\n"
            "TASK_DIR = Path(__file__).resolve().parent\n"
            "os.chdir(TASK_DIR)\n"
            "\n"
            "\n"
            "async def main():\n"
            '    llm4ad = LLM4AD("config.yaml")\n'
            "    llm4ad.print_run_summary()\n"
            "    result = await llm4ad.run()\n"
            "    if result.best_individual:\n"
            '        print(f"Best score: {result.best_individual.score:.4f}")\n'
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    asyncio.run(main())\n"
        )

    @staticmethod
    def _generate_test_evaluator(
        evaluator_file_name: str, evaluator_class_name: str
    ) -> str:
        """Generate a minimal test_evaluator.py script."""
        module_name = evaluator_file_name.removesuffix(".py")
        return (
            f'"""Test script to verify evaluator loads correctly."""\n'
            f"\n"
            f"import sys\n"
            f"from pathlib import Path\n"
            f"\n"
            f"sys.path.insert(0, str(Path(__file__).parent))\n"
            f"mod = __import__('{module_name}')\n"
            f"assert hasattr(mod, '{evaluator_class_name}'), "
            f"'Class {evaluator_class_name} not found'\n"
            f"print('Evaluator loaded successfully')\n"
        )

    @staticmethod
    def _extract_metrics_from_evaluator(evaluator_code: str) -> list[dict[str, Any]]:
        """Extract metrics from evaluator code by searching for metric patterns.

        Args:
            evaluator_code: Python source of the evaluator file.

        Returns:
            List of metric dicts with name, type, weight, description.
        """
        default_metrics = [
            {
                "name": "score",
                "type": "maximize",
                "weight": 1.0,
                "description": "Default metric",
            }
        ]

        metric_pattern = r"(?:minimize|maximize).*?([a-zA-Z_]\w*)"
        matches = re.findall(metric_pattern, evaluator_code, re.IGNORECASE)

        if matches:
            metrics = []
            for match in set(matches[:3]):
                metric_type = "minimize" if "minimize" in evaluator_code.lower() else "maximize"
                metrics.append({
                    "name": match,
                    "type": metric_type,
                    "weight": 1.0,
                    "description": f"Extracted from evaluator: {match}",
                })
            return metrics if metrics else default_metrics

        return default_metrics

    async def build_and_write(
        self,
        needs: NeedsProfile,
        output_dir: str = "./",
    ) -> tuple[TaskBlueprint, str]:
        """Build and write to disk in one step.

        Args:
            needs: Structured needs from Phase 1.
            output_dir: Parent directory for the generated project.

        Returns:
            Tuple of (validated blueprint, path to output directory).
        """
        blueprint = await self.build(needs)
        task_dir = write_task_directory(blueprint, output_dir)
        return blueprint, str(task_dir)

    async def rebuild_evaluator(
        self,
        blueprint: TaskBlueprint,
        modification_request: str,
        needs: NeedsProfile,
    ) -> TaskBlueprint:
        """Modify the evaluator code based on user feedback.

        Uses the LLM to apply the user's requested changes to the
        evaluator code, then re-validates.

        Args:
            blueprint: Current blueprint with evaluator to modify.
            modification_request: User's description of what to change.
            needs: Original needs profile for context.

        Returns:
            Updated and re-validated blueprint.

        Raises:
            BuildError: If re-validation fails after all repair attempts.
        """
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are modifying an evaluator for an algorithm design pipeline. "
                    "Apply the user's requested changes to the evaluator code. "
                    "Return ONLY the complete modified Python code, no explanations."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"## Current evaluator code:\n```python\n{blueprint.evaluator_code}\n```\n\n"
                    f"## Original problem description:\n{needs.description}\n\n"
                    f"## Requested modification:\n{modification_request}\n\n"
                    "Please provide the complete modified evaluator code."
                ),
            ),
        ]

        response = await self._provider.chat(messages)
        new_code = self._extract_code(response.text)
        if new_code:
            blueprint.evaluator_code = new_code

        # Re-validate
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self._console,
            transient=True,
        ) as progress:
            progress.add_task("Re-validating modified evaluator...", total=None)
            blueprint.validation_status = "pending"
            blueprint.validation_errors = []
            blueprint.repair_attempts = 0
            validator = TaskValidator(self._provider)
            blueprint = await validator.validate(
                blueprint, max_attempts=self._max_repair
            )

        if not blueprint.is_valid():
            errors_summary = "\n".join(f"  - {e}" for e in blueprint.validation_errors)
            raise BuildError(
                f"Re-validation failed:\n{errors_summary}",
                blueprint=blueprint,
            )

        return blueprint

    async def _analyze(self, needs: NeedsProfile) -> AnalysisResult:
        """Run TaskAnalyzer on the needs profile."""
        analyzer = TaskAnalyzer(self._provider)
        analysis = await analyzer.analyze(
            needs.description,
            code_path=Path(needs.code_path) if needs.code_path else None,
            data_path=Path(needs.data_path) if needs.data_path else None,
            multimodal=needs.multimodal,
            visualization_hint=needs.visualization_hint,
        )

        if needs.project_name:
            analysis.project_name = needs.project_name

        logger.info(
            "Analysis complete: project={}, function={}, metrics={}",
            analysis.project_name,
            analysis.function_name,
            [m["name"] for m in analysis.metrics],
        )
        return analysis

    async def _create(
        self, analysis: AnalysisResult, needs: NeedsProfile
    ) -> TaskBlueprint:
        """Run TaskCreator to generate artifacts."""
        creator = TaskCreator(self._provider)
        blueprint = await creator.create(
            analysis, needs.description, multimodal=needs.multimodal
        )
        logger.info(
            "Generation complete: evaluator={}, algorithm={}/{}",
            blueprint.evaluator_file_name,
            blueprint.algorithm_dir_name,
            blueprint.algorithm_file_name,
        )
        return blueprint

    async def _validate(
        self, blueprint: TaskBlueprint, *, skip_debug_run: bool = False
    ) -> TaskBlueprint:
        """Run TaskValidator with auto-repair."""
        validator = TaskValidator(self._provider)
        return await validator.validate(
            blueprint,
            max_attempts=self._max_repair,
            multimodal=False,
            skip_debug_run=skip_debug_run,
        )

    async def _generate_requirements(
        self, blueprint: TaskBlueprint, analysis: AnalysisResult
    ) -> TaskBlueprint:
        """Ask the LLM to produce a ``requirements.txt`` for the project.

        Runs after validation so the prompt sees the final post-repair code.
        Stores the cleaned output on ``blueprint.requirements_txt``. On any
        LLM error the field is left empty — the project is still usable, it
        just won't ship a requirements file.

        The prompt is given the analyzed ``problem_type`` and
        ``complexity_tier`` so the LLM can include not only currently-imported
        packages but also ones the algorithm is likely to need once evolved
        into a different approach for the same problem domain (e.g. ``scipy``
        and ``torch`` are commonly reached for in RL tasks even when the
        baseline code only uses ``gymnasium`` and ``numpy``).

        Args:
            blueprint: Validated blueprint with final evaluator/algorithm code.
            analysis: Structured analysis carrying problem_type and complexity_tier.

        Returns:
            The same blueprint with ``requirements_txt`` populated.
        """
        prompt = REQUIREMENTS_PROMPT.format(
            task_description=blueprint.task_description or "(none provided)",
            problem_type=analysis.problem_type or "other",
            complexity_tier=analysis.complexity_tier or "simple",
            evaluator_code=blueprint.evaluator_code,
            algorithm_code=blueprint.algorithm_code,
            test_evaluator_code=blueprint.test_evaluator_code or "(none)",
        )
        try:
            result = await self._provider.generate(prompt, temperature=0.2, max_tokens=2048)
            cleaned = self._sanitize_requirements(result.text)

            # Fallback: if LLM returned nothing useful, use problem-type baseline
            if not cleaned:
                logger.warning(
                    "LLM produced no valid requirements; applying fallback for problem_type=%s",
                    analysis.problem_type,
                )
                cleaned = self._get_fallback_requirements(analysis.problem_type)
        except Exception as e:
            logger.warning("requirements.txt generation failed: {}; applying fallback", e)
            cleaned = self._get_fallback_requirements(analysis.problem_type)

        blueprint.requirements_txt = cleaned
        if cleaned:
            n = sum(1 for line in cleaned.splitlines() if line and not line.startswith("#"))
            logger.info(
                "Stage 4 (requirements): generated %d packages "
                "(problem_type=%s, complexity_tier=%s)",
                n, analysis.problem_type, analysis.complexity_tier,
            )
        else:
            logger.info("Stage 4 (requirements): produced no packages (stdlib-only)")
        return blueprint

    @staticmethod
    def _get_fallback_requirements(problem_type: str | None) -> str:
        """Provide baseline requirements when LLM generation fails.

        Returns a minimal set of common packages for the given problem type.
        This ensures that generated tasks have at least basic dependencies
        even when LLM-based generation fails.

        Args:
            problem_type: The analyzed problem type (e.g., "rl", "ml", "computer_vision").

        Returns:
            Newline-separated package list, or empty string for unknown types.
        """
        # Baseline packages by problem type
        fallback_map = {
            "rl": ["gymnasium", "numpy", "scipy", "torch"],
            "ml": ["numpy", "pandas", "scikit-learn", "scipy"],
            "computer_vision": ["numpy", "opencv-python", "Pillow", "torch", "torchvision"],
            "image_processing": ["numpy", "opencv-python", "Pillow", "scikit-image"],
            "nlp": ["numpy", "torch", "transformers", "tokenizers"],
            "text_processing": ["numpy", "pandas", "nltk"],
            "deep_learning": ["numpy", "torch", "torchvision", "matplotlib"],
            "graph_processing": ["numpy", "networkx", "scipy"],
            "data_analysis": ["numpy", "pandas", "matplotlib", "scipy"],
            "time_series": ["numpy", "pandas", "scipy", "statsmodels"],
            "regression": ["numpy", "pandas", "scikit-learn", "scipy"],
            "combinatorial_optimization": ["numpy", "scipy", "networkx"],
            "scheduling": ["numpy", "scipy", "networkx"],
            "simulation": ["numpy", "scipy", "matplotlib"],
        }

        packages = fallback_map.get(problem_type or "other", ["numpy", "scipy"])
        return "\n".join(sorted(packages)) + "\n" if packages else ""

    @staticmethod
    def _sanitize_requirements(text: str) -> str:
        """Clean an LLM-emitted requirements.txt blob.

        - Strips ``` code fences and surrounding prose.
        - Drops blank lines and inline comments (everything after ``#``).
        - Drops anything that doesn't look like a valid pip spec line.
        - Drops llm4ad / stdlib hits in case the LLM ignored the prompt rule.
        - Deduplicates while preserving first-seen order, then sorts.

        Args:
            text: Raw LLM output.

        Returns:
            Cleaned ``requirements.txt`` content, or empty string if nothing
            usable remained.
        """
        if not text:
            return ""

        # Strip code fences.
        lines = text.strip().splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].lstrip().startswith("```"):
            lines = lines[:-1]

        # "# none" sentinel from the prompt: project needs only stdlib.
        joined = "\n".join(lines).strip().lower()
        if joined == "# none":
            return ""

        stdlib_blocklist = {
            "os", "sys", "json", "subprocess", "pathlib", "typing", "dataclasses",
            "asyncio", "re", "math", "random", "itertools", "functools",
            "collections", "hashlib", "logging", "tempfile", "shutil",
            "importlib", "concurrent", "multiprocessing", "threading", "time",
            "datetime", "io", "copy", "ast", "inspect", "argparse", "enum",
            "abc", "uuid", "csv", "pickle", "warnings", "traceback", "sqlite3",
        }

        spec_re = re.compile(
            r"^\s*([A-Za-z0-9][A-Za-z0-9._\-]*)(\[[A-Za-z0-9_,\-]+\])?\s*"
            r"((?:[<>=!~]=?|@)\s*[^\s#]+)?\s*$"
        )

        seen: set[str] = set()
        keep: list[str] = []
        for raw in lines:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            m = spec_re.match(line)
            if not m:
                continue
            name = m.group(1).lower()
            if name in stdlib_blocklist or name == "llm4ad":
                continue
            key = name
            if key in seen:
                continue
            seen.add(key)
            keep.append(line)

        keep.sort(key=str.lower)
        return "\n".join(keep) + ("\n" if keep else "")

    @staticmethod
    def _configure(
        blueprint: TaskBlueprint, analysis: AnalysisResult
    ) -> TaskBlueprint:
        """Apply rule-based evolution parameter recommendations.

        Uses ConfigRecommender to select evolution hyperparameters
        based on the complexity tier inferred during analysis.
        Replaces the evolution section and evaluator runtime settings
        in the config YAML with recommended values.

        Args:
            blueprint: Validated blueprint with default-config yaml.
            analysis: Structured analysis including complexity_tier.

        Returns:
            The same blueprint with ``config_yaml`` updated to use
            recommended evolution parameters.
        """
        recommender = ConfigRecommender()
        params = recommender.recommend(analysis.complexity_tier)

        from llm4ad.builder.config_recommender import render_default_evolution_yaml

        # Replace the default evolution block with recommended values.
        # Both strings are generated by render_evolution_yaml(), so the
        # format is identical and a direct string replace is reliable.
        old_evo_block = render_default_evolution_yaml()
        new_evo_block = render_evolution_yaml(params)
        blueprint.config_yaml = blueprint.config_yaml.replace(old_evo_block, new_evo_block)

        # Replace evaluator runtime settings
        blueprint.config_yaml = blueprint.config_yaml.replace(
            "timeout: 60.0", f"timeout: {params.evaluator_timeout}"
        )
        blueprint.config_yaml = blueprint.config_yaml.replace(
            "batch_size: 5", f"batch_size: {params.evaluator_batch_size}"
        )

        logger.info(
            "Stage 4 (configure): applied complexity_tier='%s' → islands=%d, pop=%d, gens=%d",
            analysis.complexity_tier,
            params.num_islands,
            params.island_population_size,
            params.max_generations,
        )
        return blueprint

    @staticmethod
    def _extract_code(response: str) -> str | None:
        """Extract Python code from LLM response."""
        if "```python" in response:
            start = response.index("```python") + len("```python")
            end = response.index("```", start)
            return response[start:end].strip()
        if "```" in response:
            start = response.index("```") + 3
            end = response.index("```", start)
            return response[start:end].strip()
        return response.strip() if response.strip() else None
