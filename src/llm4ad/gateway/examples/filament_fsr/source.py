"""Validate the fixed Filament shader interface before dispatch or compilation."""

import re
from pathlib import Path

from llm4ad.gateway.examples.filament_fsr import TASK_TEMPLATE
from llm4ad.gateway.sdk.errors import EvaluationConfigurationError, EvaluationError

SEED = TASK_TEMPLATE / "algorithm"
SOURCE_NAME = "ffx_fsr1_mobile.glsl"
START, END = "    // EVOLVE_START\n", "    // EVOLVE_END\n"
EVALUATOR_ID = "filament-fsr1-android"
PERFORMANCE_EVALUATOR_ID = "filament-fsr1-android-performance"
CONTRACT_VERSION = "1"
DATA_VERSION = "filament-v1.75.0-mali-helmet-orbit-fox-walk"


class CandidateShaderError(EvaluationError):
    """Report a shader defect the model can correct without retrying the same code."""

    def __init__(self, message: str) -> None:
        """Attach the candidate verdict used by the worker and the repair loop."""
        super().__init__(message, metadata={"failure_category": "candidate", "candidate_failure": True})


def split_source(source: str) -> tuple[str, str, str]:
    """Split the shader into its fixed context and its single editable body."""
    if source.count(START) != 1 or source.count(END) != 1 or source.index(END) < source.index(START):
        raise CandidateShaderError("Keep the original EVOLVE_START and EVOLVE_END markers in order")
    before, rest = source.split(START)
    body, after = rest.split(END)
    return before, body, after


def directives(source: str) -> list[str]:
    """Read preprocessor directives after normalizing line splicing and comments."""
    source = re.sub(r"\\\n", "", source)
    source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.DOTALL)
    return [" ".join(line.split()) for line in source.splitlines() if line.lstrip().startswith("#")]


def reference_source() -> tuple[str, str, str]:
    """Load the bundled reference and distinguish installation defects from candidate errors."""
    try:
        return split_source((SEED / SOURCE_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, CandidateShaderError) as error:
        raise EvaluationConfigurationError("The bundled Filament shader reference is missing or invalid") from error


def validate_candidate(source: Path, reference: tuple[str, str, str]) -> str:
    """Return a valid candidate with the platform markers removed.

    Args:
        source: Shader file at the root of the candidate worktree.
        reference: Canonical shader split into its prefix, editable body and suffix.

    Returns:
        Shader source ready for the Filament material compiler.
    """
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 64 * 1024:
        raise CandidateShaderError(f"Provide {SOURCE_NAME} at the root of the candidate worktree (maximum 64 KiB)")
    try:
        before, body, after = split_source(source.read_text(encoding="utf-8"))
    except UnicodeError as error:
        raise CandidateShaderError("The candidate shader must be UTF-8") from error
    prefix, original, suffix = reference
    if (before, after) != (prefix, suffix):
        raise CandidateShaderError("Restore the original shader outside the EVOLVE_START and EVOLVE_END markers")
    expected = directives(original)
    actual = directives(body)
    if actual != expected:
        raise CandidateShaderError(
            "Preserve every original preprocessor directive inside FsrEasuL in the same order, "
            "including repeated directives. Keep the original branches and edit the GLSL statements within them.\n"
            f"Expected directives: {' | '.join(expected)}\n"
            f"Candidate directives: {' | '.join(actual) or '(none)'}"
        )
    return before + body + after
