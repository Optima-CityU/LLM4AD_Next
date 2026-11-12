"""Common result normalization, optional file capture and exception diagnostics."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
import traceback
from pathlib import Path

from llm4ad.evaluator.models import EvaluationResult

from .errors import EvaluationError
from .journal import Reporter


def prepare_result(job, reporter: Reporter, value, elapsed_ms: float) -> dict:
    """Normalize an adapter result and spool optional evidence without changing its score."""
    from llm4ad.evaluator.records import behavior_artifacts, regular_file

    result = EvaluationResult.model_validate(value)
    if result.duration_ms == 0:
        result.duration_ms = elapsed_ms
    errors = list(result.metadata.get("artifact_errors", []))
    behavior_root = Path(job.project_root) / ".behavior-artifacts"
    try:
        for artifact in behavior_artifacts(behavior_root, result):
            reporter.artifact(
                artifact.name, behavior_root / "output" / artifact.path, artifact.media_type, description=artifact.description
            )
    except (OSError, ValueError) as exc:
        errors.append(f"Behavior capture: {type(exc).__name__}")
    for artifact in result.artifacts:
        try:
            filename = Path(artifact.path)
            if not filename.is_absolute():
                filename = Path(job.artifact_dir) / artifact.path
            filename = regular_file(filename, Path(job.artifact_dir))
            reporter.artifact(artifact.name, filename, artifact.media_type, description=artifact.description)
        except (OSError, ValueError) as exc:
            errors.append(f"{artifact.name}: {type(exc).__name__}")
    if errors:
        result.metadata["artifact_errors"] = errors
    result.artifacts = []
    return reporter.enrich(result.model_dump(mode="json"))


def exception_report(job, reporter: Reporter, error: BaseException, elapsed_ms: float) -> dict:
    """Retain the original exception and structured context through the common pipeline."""
    metadata = dict(error.metadata) if isinstance(error, EvaluationError) else {}
    metadata.setdefault("failure_type", type(error).__name__)
    feedback = error.evolution_feedback if isinstance(error, EvaluationError) else {}
    artifacts = error.artifacts if isinstance(error, EvaluationError) else []
    message = str(error) or type(error).__name__
    result = EvaluationResult(
        success=False,
        metrics={},
        error_message=message,
        duration_ms=elapsed_ms,
        metadata=metadata,
        evolution_feedback=feedback,
        artifacts=artifacts,
    )
    reporter.message(message, level="error", data={"error_type": type(error).__name__})
    outputs = {"exception": "".join(traceback.format_exception(error))}
    if isinstance(error, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
        if isinstance(error, subprocess.CalledProcessError):
            result.metadata["returncode"] = error.returncode
        else:
            result.metadata["timeout"] = error.timeout
        for name in ("stdout", "stderr"):
            if getattr(error, name, None):
                outputs[name] = getattr(error, name)
    with tempfile.TemporaryDirectory(prefix="llm4ad-evaluation-error-") as folder:
        for name, value in outputs.items():
            content = value if isinstance(value, bytes) else value.encode("utf-8", errors="replace")
            path = Path(folder) / name
            path.write_bytes(content)
            identity = hashlib.sha256(content).hexdigest()[:16]
            try:
                reporter.artifact(
                    f"diagnostics/{name}-{identity}.txt", path, "text/plain", description=f"SDK captured evaluator {name}"
                )
            except (OSError, ValueError) as exc:
                result.metadata.setdefault("artifact_errors", []).append(f"{name}: {type(exc).__name__}")
    report = prepare_result(job, reporter, result, elapsed_ms) if job else reporter.enrich(result.model_dump(mode="json"))
    return {**report, "message": message, "error_type": type(error).__name__}
