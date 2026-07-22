"""TaskValidator agent: validate and auto-repair generated blueprints.

Runs a 7-stage validation pipeline:
1. Syntax check (ast.parse) on evaluator and algorithm code
2. Config validation (YAML structure check)
3. EVOLVE markers check in algorithm code
4. Multimodal-specific checks (if multimodal=True)
5. Import check (subprocess: import evaluator, verify class exists)
6. Algorithm trial run (subprocess: run algorithm with sample input)
7. Debug run trial (subprocess: run debug_run.py)

On failure, constructs repair prompts and sends to LLM for fixes.
Tracks error signatures to detect repeating errors and escalate.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from loguru import logger

from llm4ad.builder.blueprint import TaskBlueprint
from llm4ad.builder.creator import _strip_code_fences
from llm4ad.builder.prompts import (
    REPAIR_ALGORITHM_PROMPT,
    REPAIR_DATASET_PROMPT,
    REPAIR_DEBUG_RUN_PROMPT,
    REPAIR_EVALUATOR_MULTIMODAL_PROMPT,
    REPAIR_EVALUATOR_PROMPT,
    REPAIR_FULL_PROMPT,
    REPAIR_TEST_EVALUATOR_PROMPT,
    get_algorithm_template,
    get_evaluator_template,
    get_multimodal_evaluator_template,
)
from llm4ad.builder.writer import write_task_directory
from llm4ad.infra.provider.base import BaseProvider


class TaskValidator:
    """Validate a TaskBlueprint and auto-repair on failure."""

    _RUNTIME_TIMEOUT = 30

    def __init__(self, provider: BaseProvider) -> None:
        """Initialize with an LLM provider for repair calls."""
        self._provider = provider

    async def validate(
        self,
        blueprint: TaskBlueprint,
        *,
        max_attempts: int = 10,
        multimodal: bool = False,
        skip_debug_run: bool = False,
    ) -> TaskBlueprint:
        """Validate the blueprint and repair if needed.

        Args:
            blueprint: TaskBlueprint to validate.
            max_attempts: Maximum repair attempts.
            multimodal: Whether to apply multimodal-specific validation.
            skip_debug_run: Skip the debug_run.py validation stage.

        Returns:
            Validated (possibly repaired) TaskBlueprint.
        """
        error_signatures: list[str] = []
        error_history: list[str] = []

        for attempt in range(1, max_attempts + 1):
            logger.info("Validation attempt {}/{}", attempt, max_attempts)
            error = self._run_validation_stages(
                blueprint, multimodal=multimodal, skip_debug_run=skip_debug_run
            )

            if error is None:
                blueprint.validation_status = "passed"
                blueprint.validation_errors = []
                logger.info("Validation passed on attempt {}", attempt)
                return blueprint

            # Record error
            blueprint.validation_errors.append(error)
            blueprint.repair_attempts = attempt
            error_history.append(error)
            sig = _error_signature(error)

            # Check for repeating errors — escalate to full repair
            if sig in error_signatures:
                logger.warning("Repeating error detected, escalating to full repair")
                blueprint = await self._repair_full(blueprint, multimodal=multimodal)
            else:
                error_signatures.append(sig)
                blueprint = await self._repair_targeted(
                    blueprint, error, error_history=error_history, multimodal=multimodal,
                )

        blueprint.validation_status = "failed"
        logger.error("Validation failed after {} attempts", max_attempts)
        return blueprint

    def check(
        self,
        blueprint: TaskBlueprint,
        *,
        multimodal: bool = False,
        skip_debug_run: bool = False,
    ) -> TaskBlueprint:
        """Run the validation gate once with no auto-repair.

        Unlike :meth:`validate`, this never calls the LLM to repair the
        blueprint — it only runs the validation stages a single time and
        records the outcome. Use it to re-check a blueprint whose code was
        edited by hand (e.g. by the build agent) without overwriting those
        edits with LLM-generated repairs.

        Args:
            blueprint: TaskBlueprint to check (its ``*_code`` fields are read
                as-is; the caller is responsible for loading any on-disk edits
                back onto the blueprint first).
            multimodal: Whether to apply multimodal-specific validation.
            skip_debug_run: Skip the debug_run.py validation stage.

        Returns:
            The same blueprint with ``validation_status`` set to ``"passed"``
            or ``"failed"`` and ``validation_errors`` reflecting this check.
        """
        error = self._run_validation_stages(
            blueprint, multimodal=multimodal, skip_debug_run=skip_debug_run
        )
        if error is None:
            blueprint.validation_status = "passed"
            blueprint.validation_errors = []
        else:
            blueprint.validation_status = "failed"
            blueprint.validation_errors = [error]
        return blueprint

    # ------------------------------------------------------------------
    # Validation stages
    # ------------------------------------------------------------------

    def _run_validation_stages(
        self, blueprint: TaskBlueprint, *, multimodal: bool = False, skip_debug_run: bool = False
    ) -> str | None:
        """Run all validation stages. Returns error message or None if all pass."""
        # Stage 1: Syntax check
        error = self._check_syntax("evaluator", blueprint.evaluator_code)
        if error:
            return error

        error = self._check_syntax("algorithm", blueprint.algorithm_code)
        if error:
            return error

        # Stage 2: Config validation
        error = self._check_config(blueprint.config_yaml)
        if error:
            return error

        # Stage 3: Check EVOLVE markers
        error = self._check_evolve_markers(blueprint.algorithm_code)
        if error:
            return error

        # Stage 4: Multimodal-specific checks
        if multimodal:
            error = self._check_multimodal(blueprint.evaluator_code)
            if error:
                return error

        # Stage 4.5: Check sample data exists
        if not blueprint.dataset_files:
            return "[dataset:missing] No sample data generated - SAMPLE_DATA section was empty or NONE"

        # Stage 4.6: Check test_evaluator code exists
        if not blueprint.test_evaluator_code.strip():
            return "[runtime:test_evaluator] test_evaluator.py was not generated (TEST_EVALUATOR section empty)"

        # Stage 5-7: Runtime checks
        temp_dir = None
        try:
            temp_dir = self._write_to_temp(blueprint)

            error = self._check_import(temp_dir, blueprint)
            if error:
                return error

            error = self._check_algorithm_trial(temp_dir, blueprint)
            if error:
                return error

            if not skip_debug_run:
                error = self._check_debug_run(temp_dir)
                if error:
                    return error

            error = self._check_test_evaluator(temp_dir, blueprint)
            if error:
                return error
        except Exception as e:
            logger.warning("Runtime validation setup failed: {}", e)
            return f"[runtime:setup] Failed to set up runtime validation: {e}"
        finally:
            if temp_dir is not None:
                shutil.rmtree(temp_dir.parent, ignore_errors=True)

        return None

    @staticmethod
    def _check_syntax(name: str, code: str) -> str | None:
        """Validate Python syntax using ast.parse."""
        try:
            ast.parse(code)
            return None
        except SyntaxError as e:
            return f"[syntax:{name}] {e.msg} (line {e.lineno}): {e.text}"

    @staticmethod
    def _check_config(config_yaml: str) -> str | None:
        """Validate config YAML against AppConfig schema."""
        try:
            import yaml
            data = yaml.safe_load(config_yaml)
        except Exception as e:
            return f"[config:yaml_parse] YAML parse error: {e}"

        if not isinstance(data, dict):
            return "[config:type] Config must be a YAML mapping"

        # Check required top-level fields
        for field in ("project_name", "evaluator", "evolution"):
            if field not in data:
                return f"[config:missing] Required field '{field}' not found in config"

        # Skip full Pydantic validation since it requires env vars to be set
        # Just verify the structure is reasonable
        evaluator = data.get("evaluator", {})
        if isinstance(evaluator, dict) and "module" not in evaluator and "executable" not in evaluator:
                return "[config:evaluator] Evaluator must have 'module' or 'executable' field"

        return None

    @staticmethod
    def _check_evolve_markers(algorithm_code: str) -> str | None:
        """Verify algorithm code contains EVOLVE_START and EVOLVE_END markers."""
        if "EVOLVE_START" not in algorithm_code:
            return "[algorithm:markers] Missing EVOLVE_START marker in algorithm code"
        if "EVOLVE_END" not in algorithm_code:
            return "[algorithm:markers] Missing EVOLVE_END marker in algorithm code"

        # Verify order
        start_idx = algorithm_code.index("EVOLVE_START")
        end_idx = algorithm_code.index("EVOLVE_END")
        if start_idx >= end_idx:
            return "[algorithm:markers] EVOLVE_START must come before EVOLVE_END"

        return None

    @staticmethod
    def _check_multimodal(evaluator_code: str) -> str | None:
        """Verify evaluator contains required multimodal patterns."""
        if "BehaviorData" not in evaluator_code:
            return "[multimodal:import] Missing BehaviorData import in evaluator"
        if "BehaviorVisualization" not in evaluator_code:
            return "[multimodal:import] Missing BehaviorVisualization import in evaluator"
        if "BaseRenderer" not in evaluator_code:
            return "[multimodal:renderer] Missing BaseRenderer import/subclass in evaluator"
        if "behavior_storage" not in evaluator_code:
            return "[multimodal:storage] Missing behavior_storage handling in evaluator"
        return None

    # ------------------------------------------------------------------
    # Runtime validation stages
    # ------------------------------------------------------------------

    @staticmethod
    def _write_to_temp(blueprint: TaskBlueprint) -> Path:
        """Write blueprint to a temporary directory for runtime testing."""
        temp_base = Path(tempfile.mkdtemp(prefix="llm4ad_validate_"))
        task_dir = write_task_directory(blueprint, temp_base)
        return task_dir

    @staticmethod
    def _get_src_path() -> str:
        """Get the src/ directory path for subprocess PYTHONPATH."""
        # validator.py is at src/llm4ad/builder/validator.py → src/ is 3 levels up
        return str(Path(__file__).resolve().parent.parent.parent)

    def _subprocess_env(self) -> dict[str, str]:
        """Build environment for subprocess with correct PYTHONPATH."""
        env = os.environ.copy()
        src_path = self._get_src_path()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{src_path}{os.pathsep}{existing}" if existing else src_path
        return env

    @staticmethod
    def _format_subprocess_failure(
        stage: str,
        result: subprocess.CompletedProcess[str],
        *,
        cmd: list[str] | None = None,
        cwd: str | None = None,
        extra: str | None = None,
        max_chars: int = 4000,
    ) -> str:
        """Format a rich subprocess failure message for LLM repair.

        Includes exit code, command, cwd, stdout, and stderr — full content
        rather than the last 500 chars, so the LLM sees the actual root
        cause rather than the trailing tail of a traceback.

        Args:
            stage: Validation stage prefix (e.g. ``[runtime:algorithm]``).
            result: Completed subprocess.
            cmd: Command list executed (logged for the LLM).
            cwd: Working directory used.
            extra: Optional extra context line (e.g. sample input).
            max_chars: Soft cap for stdout/stderr each (Pythonpath traces can
                run long; default 4000 keeps the prompt under control while
                preserving the full traceback in nearly all real cases).

        Returns:
            A multi-line error string consumable by repair prompts.
        """
        parts: list[str] = [f"{stage} exit code {result.returncode}"]
        if cmd:
            parts.append(f"  cmd: {' '.join(str(c) for c in cmd)}")
        if cwd:
            parts.append(f"  cwd: {cwd}")
        if extra:
            parts.append(f"  context: {extra}")

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if stdout:
            tail = stdout if len(stdout) <= max_chars else "...(truncated)...\n" + stdout[-max_chars:]
            parts.append(f"  stdout:\n{tail}")
        if stderr:
            tail = stderr if len(stderr) <= max_chars else "...(truncated)...\n" + stderr[-max_chars:]
            parts.append(f"  stderr:\n{tail}")
        return "\n".join(parts)

    def _check_import(self, task_dir: Path, blueprint: TaskBlueprint) -> str | None:
        """Stage 5: Verify evaluator module imports and class exists."""
        evaluator_module = blueprint.evaluator_file_name.removesuffix(".py")
        script = (
            f"import sys; sys.path.insert(0, r'{task_dir}'); "
            f"mod = __import__('{evaluator_module}'); "
            f"assert hasattr(mod, '{blueprint.evaluator_class_name}'), "
            f"'Class {blueprint.evaluator_class_name} not found in {evaluator_module}'"
        )
        cmd = [sys.executable, "-c", script]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=self._RUNTIME_TIMEOUT,
                env=self._subprocess_env(),
            )
        except subprocess.TimeoutExpired:
            return "[runtime:import] Evaluator import timed out"

        if result.returncode != 0:
            return self._format_subprocess_failure(
                "[runtime:import]", result, cmd=cmd, cwd=str(task_dir),
                extra=f"importing module '{evaluator_module}', expecting class '{blueprint.evaluator_class_name}'",
            )
        return None

    def _check_algorithm_trial(self, task_dir: Path, blueprint: TaskBlueprint) -> str | None:
        """Stage 6: Run algorithm with sample data and verify JSON output."""
        algo_file = task_dir / blueprint.algorithm_dir_name / blueprint.algorithm_file_name
        if not algo_file.exists():
            return f"[runtime:algorithm] Algorithm file not found: {algo_file.name}"

        sample_json = next(iter(blueprint.dataset_files.values())).strip()
        cmd = [sys.executable, str(algo_file), sample_json]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=self._RUNTIME_TIMEOUT,
                cwd=str(task_dir),
                env=self._subprocess_env(),
            )
        except subprocess.TimeoutExpired:
            return "[runtime:algorithm] Algorithm execution timed out"

        if result.returncode != 0:
            sample_preview = sample_json if len(sample_json) <= 500 else sample_json[:500] + "...(truncated)"
            return self._format_subprocess_failure(
                "[runtime:algorithm]", result, cmd=cmd, cwd=str(task_dir),
                extra=f"sample input passed via argv[1]: {sample_preview}",
            )

        stdout = result.stdout.strip()
        if stdout:
            try:
                json.loads(stdout)
            except json.JSONDecodeError as e:
                preview = stdout if len(stdout) <= 1000 else stdout[:1000] + "...(truncated)"
                return (
                    f"[runtime:algorithm] Output is not valid JSON: {e}\n"
                    f"  stdout:\n{preview}"
                )

        return None

    def _check_debug_run(self, task_dir: Path) -> str | None:
        """Stage 7: Run debug_run.py and verify it exits successfully."""
        debug_script = task_dir / "debug_run.py"
        if not debug_script.exists():
            return "[runtime:debug_run] debug_run.py not found"

        cmd = [sys.executable, str(debug_script)]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=self._RUNTIME_TIMEOUT,
                cwd=str(task_dir),
                env=self._subprocess_env(),
            )
        except subprocess.TimeoutExpired:
            return "[runtime:debug_run] debug_run.py timed out"

        if result.returncode != 0:
            return self._format_subprocess_failure(
                "[runtime:debug_run]", result, cmd=cmd, cwd=str(task_dir),
            )
        return None

    def _check_test_evaluator(self, task_dir: Path, blueprint: TaskBlueprint) -> str | None:
        """Stage 8: Run test_evaluator.py and verify evaluator works end-to-end."""
        test_script = task_dir / "test_evaluator.py"
        if not test_script.exists():
            return "[runtime:test_evaluator] test_evaluator.py not found"

        cmd = [sys.executable, str(test_script)]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=self._RUNTIME_TIMEOUT * 4,
                cwd=str(task_dir),
                env=self._subprocess_env(),
            )
        except subprocess.TimeoutExpired:
            return "[runtime:test_evaluator] test_evaluator.py timed out"

        if result.returncode != 0:
            return self._format_subprocess_failure(
                "[runtime:test_evaluator]", result, cmd=cmd, cwd=str(task_dir),
            )

        if "[FAIL]" in result.stdout:
            return self._format_subprocess_failure(
                "[runtime:test_evaluator]", result, cmd=cmd, cwd=str(task_dir),
                extra="exit code 0 but stdout contains [FAIL] — evaluator returned but a metric/assertion failed",
            )

        return None

    # ------------------------------------------------------------------
    # Repair logic
    # ------------------------------------------------------------------

    async def _repair_targeted(
        self,
        blueprint: TaskBlueprint,
        error: str,
        *,
        error_history: list[str] | None = None,
        multimodal: bool = False,
    ) -> TaskBlueprint:
        """Repair a specific artifact based on the error.

        Args:
            blueprint: Blueprint with the failing artifact.
            error: Latest validation error message (rich format).
            error_history: Full chronological list of past errors so the LLM
                can see what was already tried — prevents go-around loops.
            multimodal: Whether multimodal-specific prompts apply.
        """
        logger.info("Repairing: {}", error[:100])
        history_section = _format_history_section(error_history)

        evaluator_repair_prefixes = (
            "[syntax:evaluator]", "[config:evaluator]", "[multimodal:",
            "[runtime:import]", "[runtime:setup]",
        )
        algorithm_repair_prefixes = (
            "[syntax:algorithm]", "[algorithm:", "[runtime:algorithm]",
        )

        if error.startswith("[runtime:debug_run]"):
            blueprint.debug_run_code = await self._repair_debug_run(
                blueprint, error, history_section=history_section,
            )
        elif error.startswith("[runtime:test_evaluator]"):
            if "test_evaluator" in error.lower() and ("import" in error.lower() or "syntax" in error.lower()):
                blueprint.test_evaluator_code = await self._repair_test_evaluator(
                    blueprint, error, history_section=history_section,
                )
            else:
                blueprint.evaluator_code = await self._repair_evaluator(
                    blueprint.evaluator_code, error,
                    history_section=history_section, multimodal=multimodal,
                )
                blueprint.test_evaluator_code = await self._repair_test_evaluator(
                    blueprint, error, history_section=history_section,
                )
        elif error.startswith("[dataset:"):
            sample_data = await self._repair_dataset(blueprint, error)
            blueprint.dataset_files["data/sample/instance_001.json"] = sample_data
        elif any(error.startswith(p) for p in evaluator_repair_prefixes):
            blueprint.evaluator_code = await self._repair_evaluator(
                blueprint.evaluator_code, error,
                history_section=history_section, multimodal=multimodal,
            )
        elif any(error.startswith(p) for p in algorithm_repair_prefixes):
            # Check if the error is actually a JSON parsing issue in sample data
            if "parsing input JSON" in error or "json.decoder.JSONDecodeError" in error.lower():
                sample_data = await self._repair_dataset(blueprint, error)
                blueprint.dataset_files["data/sample/instance_001.json"] = sample_data
            elif blueprint.reuse_algorithm:
                logger.warning(
                    "Algorithm error in reuse mode (user-provided code cannot be auto-repaired): {}",
                    error[:200],
                )
            else:
                blueprint.algorithm_code = await self._repair_algorithm(
                    blueprint.algorithm_code, error, history_section=history_section,
                )
        elif error.startswith("[config:"):
            # Config errors are harder to repair via LLM since config is programmatic
            # Log and let full repair handle it
            logger.warning("Config error requires manual investigation: {}", error)
        else:
            # Generic error — try repairing evaluator first as it's the most complex
            blueprint.evaluator_code = await self._repair_evaluator(
                blueprint.evaluator_code, error,
                history_section=history_section, multimodal=multimodal,
            )

        return blueprint

    async def _repair_evaluator(
        self, evaluator_code: str, error: str, *,
        history_section: str = "", multimodal: bool = False,
    ) -> str:
        """Send repair prompt for evaluator code."""
        if multimodal:
            prompt = REPAIR_EVALUATOR_MULTIMODAL_PROMPT.format(
                evaluator_code=evaluator_code,
                error_message=error,
                history_section=history_section,
                evaluator_template=get_multimodal_evaluator_template(),
            )
        else:
            prompt = REPAIR_EVALUATOR_PROMPT.format(
                evaluator_code=evaluator_code,
                error_message=error,
                history_section=history_section,
                evaluator_template=get_evaluator_template(),
            )
        result = await self._provider.generate(prompt, temperature=0.2, max_tokens=8192)
        return _strip_code_fences(result.text.strip())

    async def _repair_algorithm(
        self, algorithm_code: str, error: str, *, history_section: str = "",
    ) -> str:
        """Send repair prompt for algorithm code."""
        prompt = REPAIR_ALGORITHM_PROMPT.format(
            algorithm_code=algorithm_code,
            error_message=error,
            history_section=history_section,
            algorithm_template=get_algorithm_template(),
        )
        result = await self._provider.generate(prompt, temperature=0.2, max_tokens=4096)
        return _strip_code_fences(result.text.strip())

    async def _repair_debug_run(
        self, blueprint: TaskBlueprint, error: str, *, history_section: str = "",
    ) -> str:
        """Send repair prompt for debug_run.py code."""
        algo_module = blueprint.algorithm_file_name.removesuffix(".py")
        prompt = REPAIR_DEBUG_RUN_PROMPT.format(
            debug_run_code=blueprint.debug_run_code,
            error_message=error,
            history_section=history_section,
            algorithm_code=blueprint.algorithm_code,
            algorithm_dir_name=blueprint.algorithm_dir_name,
            algorithm_file_name=blueprint.algorithm_file_name,
            algorithm_module=algo_module,
            function_name=blueprint.function_to_evolve,
        )
        result = await self._provider.generate(prompt, temperature=0.2, max_tokens=4096)
        return _strip_code_fences(result.text.strip())

    async def _repair_test_evaluator(
        self, blueprint: TaskBlueprint, error: str, *, history_section: str = "",
    ) -> str:
        """Send repair prompt for test_evaluator.py code."""
        metric_names = [m.get("name", "") for m in blueprint.metrics]
        prompt = REPAIR_TEST_EVALUATOR_PROMPT.format(
            test_evaluator_code=blueprint.test_evaluator_code or "(empty)",
            error_message=error,
            history_section=history_section,
            evaluator_file_name=blueprint.evaluator_file_name,
            evaluator_class_name=blueprint.evaluator_class_name,
            metric_names=json.dumps(metric_names),
        )
        result = await self._provider.generate(prompt, temperature=0.2, max_tokens=4096)
        return _strip_code_fences(result.text.strip())

    async def _repair_dataset(self, blueprint: TaskBlueprint, error: str) -> str:
        """Generate sample data when missing."""
        prompt = REPAIR_DATASET_PROMPT.format(
            project_name=blueprint.project_name,
            function_name=blueprint.function_to_evolve,
            input_format=f"JSON data for {blueprint.function_to_evolve}",
            output_format="JSON result",
            algorithm_code=blueprint.algorithm_code,
        )
        result = await self._provider.generate(prompt, temperature=0.3, max_tokens=2048)
        text = _strip_code_fences(result.text.strip())

        # Validate and clean JSON
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            # Try to extract JSON object or array
            for start_char, end_char in [("{", "}"), ("[", "]")]:
                start = text.find(start_char)
                end = text.rfind(end_char)
                if start != -1 and end > start:
                    candidate = text[start:end + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        continue
            # If all extraction attempts fail, return original and let validation catch it
            return text

    async def _repair_full(self, blueprint: TaskBlueprint, *, multimodal: bool = False) -> TaskBlueprint:
        """Full regeneration of evaluator and algorithm."""
        eval_template = get_multimodal_evaluator_template() if multimodal else get_evaluator_template()
        algo_template = get_algorithm_template()
        error_history = "\n".join(
            f"Attempt {i + 1}: {e}" for i, e in enumerate(blueprint.validation_errors)
        )
        prompt = REPAIR_FULL_PROMPT.format(
            project_name=blueprint.project_name,
            function_name=blueprint.function_to_evolve,
            function_signature=f"def {blueprint.function_to_evolve}(data):",
            function_description=blueprint.task_description,
            metrics_json=json.dumps(blueprint.metrics, indent=2),
            algorithm_dir_name=blueprint.algorithm_dir_name,
            algorithm_file_name=blueprint.algorithm_file_name,
            error_history=error_history,
            evaluator_template=eval_template,
            algorithm_template=algo_template,
        )
        result = await self._provider.generate(prompt, temperature=0.3, max_tokens=8192)
        sections = _parse_repair_sections(result.text)

        if "EVALUATOR_CODE" in sections:
            blueprint.evaluator_code = sections["EVALUATOR_CODE"]
        if "ALGORITHM_CODE" in sections and not blueprint.reuse_algorithm:
            blueprint.algorithm_code = sections["ALGORITHM_CODE"]
        if "DEBUG_RUN" in sections:
            blueprint.debug_run_code = sections["DEBUG_RUN"]
        if "TEST_EVALUATOR" in sections:
            blueprint.test_evaluator_code = sections["TEST_EVALUATOR"]

        return blueprint


def _error_signature(error: str) -> str:
    """Compute a short signature for error deduplication."""
    # Use the error prefix (e.g., [syntax:evaluator]) plus a content hash
    prefix = error.split("]")[0] + "]" if "]" in error else ""
    content_hash = hashlib.md5(error.encode()).hexdigest()[:8]
    return f"{prefix}:{content_hash}"


def _format_history_section(error_history: list[str] | None, *, max_per_entry: int = 1500) -> str:
    """Render prior repair attempts as a prompt section.

    Returns an empty string for the first attempt (nothing to summarize yet),
    and a numbered "## Previous Repair Attempts" block thereafter. The latest
    error is always shown in full elsewhere; this section captures *what was
    already tried* so the LLM can avoid the same fix twice.

    Args:
        error_history: Chronological list of all errors seen so far,
            including the current one.
        max_per_entry: Per-entry character cap to keep the prompt bounded.

    Returns:
        A formatted prompt block (empty string if no history exists yet).
    """
    if not error_history or len(error_history) < 2:
        return ""

    prior = error_history[:-1]
    lines = ["## Previous Repair Attempts (already tried — avoid same fix)"]
    for i, e in enumerate(prior, start=1):
        snippet = e if len(e) <= max_per_entry else e[:max_per_entry] + "...(truncated)"
        lines.append(f"### Attempt {i}")
        lines.append(snippet)
    lines.append("")
    return "\n".join(lines)


def _parse_repair_sections(text: str) -> dict[str, str]:
    """Parse delimited sections from repair response."""
    sections: dict[str, str] = {}
    pattern = r"===([A-Z_]+)==="
    parts = re.split(pattern, text)

    i = 1
    while i < len(parts) - 1:
        name = parts[i].strip()
        content = parts[i + 1].strip()
        content = _strip_code_fences(content)
        sections[name] = content
        i += 2

    return sections
