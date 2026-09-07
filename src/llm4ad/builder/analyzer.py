"""TaskAnalyzer agent: understand the problem and produce a structured spec.

Supports two entry modes:
- from_description: analyze a natural language problem description
- from_code: analyze existing codebase + optional data to identify what to evolve
"""

from __future__ import annotations

import ast
import contextlib
import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from llm4ad.builder.blueprint import AnalysisResult
from llm4ad.builder.prompts import (
    ANALYZE_CODE_PROMPT,
    ANALYZE_CODE_WITH_EVOLVE_PROMPT,
    ANALYZE_DESCRIPTION_PROMPT,
    ANALYZE_MULTIMODAL_SUPPLEMENT,
    CLASSIFY_EVOLVE_ROLE_PROMPT,
)
from llm4ad.infra.provider.base import BaseProvider
from llm4ad.infra.repo_analyzer.base import EvolveBlock

# Canonical evaluation-contract identifiers. `separate_script` is Variant A:
# the evaluator spawns `python algo.py '<instance>'` for a one-shot call.
# `self_spawn` is Variant B: the evaluator spawns ITSELF and drives an
# environment loop that calls a policy function many times per episode.
_SEPARATE_SCRIPT = "separate_script"
_SELF_SPAWN = "self_spawn"

# Legacy / free-form aliases the LLM (or older specs) may emit, mapped onto the
# two canonical contracts. Anything unrecognized falls back to separate_script.
_EVALUATION_PATTERN_ALIASES = {
    "separate_script": _SEPARATE_SCRIPT,
    "separate-script": _SEPARATE_SCRIPT,
    "subprocess": _SEPARATE_SCRIPT,
    "variant_a": _SEPARATE_SCRIPT,
    "variant-a": _SEPARATE_SCRIPT,
    "a": _SEPARATE_SCRIPT,
    "one_shot": _SEPARATE_SCRIPT,
    "one-shot": _SEPARATE_SCRIPT,
    "oneshot": _SEPARATE_SCRIPT,
    "stateless": _SEPARATE_SCRIPT,
    "self_spawn": _SELF_SPAWN,
    "self-spawn": _SELF_SPAWN,
    "selfspawn": _SELF_SPAWN,
    "variant_b": _SELF_SPAWN,
    "variant-b": _SELF_SPAWN,
    "b": _SELF_SPAWN,
    "policy": _SELF_SPAWN,
    "interactive": _SELF_SPAWN,
    "stateful": _SELF_SPAWN,
    "rl": _SELF_SPAWN,
    "simulation": _SELF_SPAWN,
    "episode": _SELF_SPAWN,
}


def _normalize_evaluation_pattern(raw: Any) -> str:
    """Map a raw evaluation_pattern value onto a canonical contract identifier.

    Args:
        raw: The value from the LLM spec (may be None, a canonical value, or a
            legacy/free-form alias).

    Returns:
        Either ``separate_script`` (Variant A, one-shot) or ``self_spawn``
        (Variant B, policy-in-a-loop). Unrecognized values default to
        ``separate_script`` since it is the simpler and more common contract.
    """
    if not isinstance(raw, str):
        return _SEPARATE_SCRIPT
    return _EVALUATION_PATTERN_ALIASES.get(raw.strip().lower(), _SEPARATE_SCRIPT)


class TaskAnalyzer:
    """Analyze a problem description or codebase to produce a structured spec."""

    def __init__(self, provider: BaseProvider) -> None:
        """Initialize with an LLM provider for analysis calls."""
        self._provider = provider

    async def analyze(
        self,
        description: str,
        *,
        code_path: Path | None = None,
        data_path: Path | None = None,
        multimodal: bool = False,
        visualization_hint: str | None = None,
    ) -> AnalysisResult:
        """Analyze the problem and return a structured specification.

        Args:
            description: Natural language problem description.
            code_path: Optional path to existing algorithm code.
            data_path: Optional path to dataset files.
            multimodal: Whether to include visualization specification.
            visualization_hint: Optional user-provided visualization requirements.

        Returns:
            AnalysisResult with all fields populated.
        """
        if code_path is not None:
            return await self._analyze_from_code(
                description,
                code_path,
                data_path,
                multimodal=multimodal,
                visualization_hint=visualization_hint,
            )
        return await self._analyze_from_description(
            description,
            multimodal=multimodal,
            visualization_hint=visualization_hint,
        )

    # ------------------------------------------------------------------
    # from_description mode
    # ------------------------------------------------------------------

    async def _analyze_from_description(
        self,
        description: str,
        *,
        multimodal: bool = False,
        visualization_hint: str | None = None,
    ) -> AnalysisResult:
        """Analyze a text-only problem description."""
        prompt = ANALYZE_DESCRIPTION_PROMPT.format(description=description)
        if multimodal:
            user_viz_hint = (
                f"The user has provided the following visualization requirements — "
                f"incorporate them into the visualization_spec:\n{visualization_hint}"
                if visualization_hint
                else "The user did not specify visualization requirements. "
                "Design an appropriate visualization based on the problem description."
            )
            prompt += ANALYZE_MULTIMODAL_SUPPLEMENT.format(user_viz_hint=user_viz_hint)
        result = await self._provider.generate(prompt, temperature=0.3)
        spec = self._parse_json_response(result.text)
        analysis = self._spec_to_analysis(spec)
        if multimodal:
            analysis.visualization_spec = spec.get("visualization_spec")
        return analysis

    # ------------------------------------------------------------------
    # from_code mode
    # ------------------------------------------------------------------

    async def _analyze_from_code(
        self,
        description: str,
        code_path: Path,
        data_path: Path | None,
        *,
        multimodal: bool = False,
        visualization_hint: str | None = None,
    ) -> AnalysisResult:
        """Analyze existing code and optional data.

        If the code contains EVOLVE markers, the algorithm is reused verbatim
        and only metadata (metrics, background, etc.) is inferred by the LLM.
        Otherwise, falls back to the original full-generation analysis.
        """
        from llm4ad.infra.repo_analyzer.evolve_detector import EvolveDetector

        detector = EvolveDetector(
            {
                "context_lines_before": 10,
                "context_lines_after": 10,
                "include": ["*.py"],
            }
        )
        analyzed_repo = detector.analyze(code_path)

        if analyzed_repo.evolvable_blocks:
            block = analyzed_repo.evolvable_blocks[0]
            if len(analyzed_repo.evolvable_blocks) > 1:
                logger.warning(
                    "Found {} EVOLVE blocks, using first one in {}",
                    len(analyzed_repo.evolvable_blocks),
                    block.file_path,
                )
            return await self._analyze_from_code_with_evolve(
                description,
                code_path,
                data_path,
                block,
                multimodal=multimodal,
                visualization_hint=visualization_hint,
            )

        return await self._analyze_from_code_no_evolve(
            description,
            code_path,
            data_path,
            multimodal=multimodal,
            visualization_hint=visualization_hint,
        )

    # ------------------------------------------------------------------
    # from_code mode — without EVOLVE markers (original behavior)
    # ------------------------------------------------------------------

    async def _analyze_from_code_no_evolve(
        self,
        description: str,
        code_path: Path,
        data_path: Path | None,
        *,
        multimodal: bool = False,
        visualization_hint: str | None = None,
    ) -> AnalysisResult:
        """Analyze existing code without EVOLVE markers (original from_code path)."""
        code_summary = self._build_code_summary(code_path)
        dataset_summary = (
            self._build_dataset_summary(data_path) if data_path else "No dataset provided."
        )

        prompt = ANALYZE_CODE_PROMPT.format(
            description=description,
            code_summary=code_summary,
            dataset_summary=dataset_summary,
        )
        if multimodal:
            user_viz_hint = (
                f"The user has provided the following visualization requirements — "
                f"incorporate them into the visualization_spec:\n{visualization_hint}"
                if visualization_hint
                else "The user did not specify visualization requirements. "
                "Design an appropriate visualization based on the problem description."
            )
            prompt += ANALYZE_MULTIMODAL_SUPPLEMENT.format(user_viz_hint=user_viz_hint)
        result = await self._provider.generate(prompt, temperature=0.3)
        spec = self._parse_json_response(result.text)

        analysis = self._spec_to_analysis(spec)
        analysis.existing_code_summary = spec.get("existing_code_analysis")
        analysis.dataset_summary = dataset_summary if data_path else None
        if multimodal:
            analysis.visualization_spec = spec.get("visualization_spec")

        # Store file contents for TaskCreator to use
        code_files: dict[str, str] = {}
        for py_file in sorted(code_path.rglob("*.py")):
            rel = py_file.relative_to(code_path)
            if not any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
                with contextlib.suppress(Exception):
                    code_files[str(rel)] = py_file.read_text(encoding="utf-8")
        analysis.existing_code_files = code_files if code_files else None
        return analysis

    # ------------------------------------------------------------------
    # from_code mode — with EVOLVE markers (reuse algorithm)
    # ------------------------------------------------------------------

    async def _analyze_from_code_with_evolve(
        self,
        description: str,
        code_path: Path,
        data_path: Path | None,
        block: EvolveBlock,
        *,
        multimodal: bool = False,
        visualization_hint: str | None = None,
    ) -> AnalysisResult:
        """Analyze code that has EVOLVE markers, reusing the algorithm verbatim."""
        full_code = block.absolute_path.read_text(encoding="utf-8")
        func_name, func_sig = self._extract_function_from_evolve_block(
            block.original_content,
        )

        algo_file_name = Path(block.file_path).name
        parent_dir = Path(block.file_path).parent
        algo_dir_name = str(parent_dir) if str(parent_dir) != "." else "algorithm"

        dataset_summary = (
            self._build_dataset_summary(data_path) if data_path else "No dataset provided."
        )

        prompt = ANALYZE_CODE_WITH_EVOLVE_PROMPT.format(
            description=description,
            algorithm_code=full_code,
            evolve_block_content=block.original_content,
            function_name=func_name,
            function_signature=func_sig,
            dataset_summary=dataset_summary,
            algorithm_dir_name=algo_dir_name,
            algorithm_file_name=algo_file_name,
        )
        if multimodal:
            user_viz_hint = (
                f"The user has provided the following visualization requirements — "
                f"incorporate them into the visualization_spec:\n{visualization_hint}"
                if visualization_hint
                else "The user did not specify visualization requirements. "
                "Design an appropriate visualization based on the problem description."
            )
            prompt += ANALYZE_MULTIMODAL_SUPPLEMENT.format(user_viz_hint=user_viz_hint)

        result = await self._provider.generate(prompt, temperature=0.3)
        spec = self._parse_json_response(result.text)

        analysis = self._spec_to_analysis(spec)
        analysis.has_evolve_markers = True
        analysis.algorithm_full_code = full_code
        analysis.evolve_block_content = block.original_content
        analysis.function_name = func_name
        analysis.function_signature = func_sig
        analysis.algorithm_file_name = algo_file_name
        analysis.algorithm_dir_name = algo_dir_name
        analysis.existing_code_files = {block.file_path: full_code}
        analysis.dataset_summary = dataset_summary if data_path else None
        if multimodal:
            analysis.visualization_spec = spec.get("visualization_spec")

        # Classify the EVOLVE function's role
        classifier_result = await self._classify_evolve_role(
            description, full_code, block.original_content, func_sig
        )
        analysis.function_role = classifier_result.get("function_role", "complete_solver")
        analysis.input_schema = classifier_result.get("input_schema")
        analysis.output_schema = classifier_result.get("output_schema")
        analysis.needed_helpers = classifier_result.get("needed_helpers", [])
        analysis.driver_strategy = classifier_result.get("driver_strategy", "")

        if "feasibility_warning" in classifier_result:
            logger.warning(
                "EVOLVE block feasibility concern: {}",
                classifier_result["feasibility_warning"],
            )

        logger.info(
            "EVOLVE markers detected: reusing algorithm from {} (function: {}, role: {})",
            block.file_path,
            func_name,
            analysis.function_role,
        )
        return analysis

    async def _classify_evolve_role(
        self,
        description: str,
        full_code: str,
        evolve_block: str,
        function_signature: str,
    ) -> dict[str, Any]:
        """Classify the EVOLVE function's semantic role and extract schema info.

        Args:
            description: User's problem description.
            full_code: Complete original file content.
            evolve_block: Code between EVOLVE_START and EVOLVE_END.
            function_signature: The function's signature line.

        Returns:
            Dict with function_role, input_schema, output_schema, needed_helpers,
            driver_strategy, and optionally feasibility_warning.
        """
        prompt = CLASSIFY_EVOLVE_ROLE_PROMPT.format(
            description=description,
            full_code=full_code,
            evolve_block=evolve_block,
            function_signature=function_signature,
        )
        result = await self._provider.generate(prompt, temperature=0.2)
        return self._parse_json_response(result.text)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_function_from_evolve_block(block_content: str) -> tuple[str, str]:
        """Extract function name and signature from an EVOLVE block.

        Args:
            block_content: Source code between EVOLVE_START and EVOLVE_END.

        Returns:
            Tuple of (function_name, function_signature).
        """
        try:
            tree = ast.parse(block_content)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    if node.name.startswith("_"):
                        continue
                    lines = block_content.splitlines()
                    sig_line = lines[node.lineno - 1].strip()
                    return node.name, sig_line
        except SyntaxError:
            pass

        match = re.search(
            r"^(def\s+(\w+)\s*\(.*?\)\s*(?:->.*?)?:)",
            block_content,
            re.MULTILINE | re.DOTALL,
        )
        if match:
            return match.group(2), match.group(1).strip()

        return "my_algorithm", "def my_algorithm(data):"

    def _build_code_summary(self, code_path: Path) -> str:
        """Build a compact summary of a codebase for the LLM prompt."""
        parts: list[str] = []
        py_files = sorted(code_path.rglob("*.py"))
        py_files = [
            f
            for f in py_files
            if not any(
                p.startswith(".") or p == "__pycache__" for p in f.relative_to(code_path).parts
            )
        ]

        if not py_files:
            return "No Python files found in the provided directory."

        for py_file in py_files[:15]:
            rel = py_file.relative_to(code_path)
            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue

            functions = self._extract_function_signatures(content)
            parts.append(f"### {rel}")
            if functions:
                parts.append("Functions:")
                for sig in functions:
                    parts.append(f"  - {sig}")

            # Include full content for small files, truncated for large
            if len(content) <= 3000:
                parts.append(f"```python\n{content}\n```")
            else:
                parts.append(
                    f"```python\n{content[:3000]}\n... (truncated, {len(content)} chars total)\n```"
                )
            parts.append("")

        return "\n".join(parts)

    @staticmethod
    def _extract_function_signatures(source: str) -> list[str]:
        """Extract function/class signatures from Python source using AST."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        signatures: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                arg_names = [a.arg for a in node.args.args]
                sig = f"def {node.name}({', '.join(arg_names)})"
                signatures.append(sig)
            elif isinstance(node, ast.ClassDef):
                signatures.append(f"class {node.name}")
        return signatures

    def _build_dataset_summary(self, data_path: Path) -> str:
        """Peek at dataset files to build a summary."""
        if not data_path.exists():
            return f"Data path does not exist: {data_path}"

        parts: list[str] = [f"Dataset directory: {data_path}"]
        files = sorted(data_path.rglob("*"))
        files = [f for f in files if f.is_file()]

        parts.append(f"Total files: {len(files)}")
        if not files:
            return "\n".join(parts)

        # Show first 3 files
        for f in files[:3]:
            rel = f.relative_to(data_path)
            parts.append(f"\n### {rel} ({f.stat().st_size} bytes)")
            try:
                content = f.read_text(encoding="utf-8")
                if len(content) <= 1000:
                    parts.append(f"```\n{content}\n```")
                else:
                    parts.append(f"```\n{content[:1000]}\n... (truncated)\n```")
            except Exception:
                parts.append("(binary or unreadable)")

        if len(files) > 3:
            parts.append(f"\n... and {len(files) - 3} more files")

        return "\n".join(parts)

    def _parse_json_response(self, text: str) -> dict[str, Any]:
        """Extract and parse JSON from LLM response text."""
        # Try direct parse first
        text = text.strip()
        if text.startswith("```"):
            # Strip markdown code fences
            lines = text.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            result = json.loads(text)
            return result if isinstance(result, dict) else {}
        except json.JSONDecodeError:
            pass

        # Try to find JSON object in the text
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                result = json.loads(text[start : end + 1])
                return result if isinstance(result, dict) else {}
            except json.JSONDecodeError:
                pass

        logger.error("Failed to parse JSON from LLM response:\n{}", text[:500])
        raise ValueError(f"Could not parse JSON from LLM response: {text[:200]}")

    def _spec_to_analysis(self, spec: dict[str, Any]) -> AnalysisResult:
        """Convert a raw JSON spec dict to an AnalysisResult."""
        return AnalysisResult(
            problem_type=spec.get("problem_type", "other"),
            complexity_tier=spec.get("complexity_tier", "simple"),
            evaluation_pattern=_normalize_evaluation_pattern(spec.get("evaluation_pattern")),
            function_name=spec.get("function_name", "my_algorithm"),
            function_signature=spec.get("function_signature", "def my_algorithm(data):"),
            function_description=spec.get("function_description", ""),
            metrics=spec.get(
                "metrics",
                [
                    {
                        "name": "score",
                        "type": "maximize",
                        "weight": 1.0,
                        "description": "Primary score",
                    }
                ],
            ),
            input_format=spec.get("input_format", ""),
            output_format=spec.get("output_format", ""),
            algorithm_dir_name=spec.get("algorithm_dir_name", "algorithm"),
            algorithm_file_name=spec.get("algorithm_file_name", "solve.py"),
            project_name=spec.get("project_name", "my_task"),
            background=spec.get("background", ""),
            existing_code_summary=spec.get("existing_code_analysis"),
            dataset_summary=spec.get("dataset_description"),
        )
