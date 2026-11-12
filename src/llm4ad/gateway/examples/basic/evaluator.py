"""Runnable custom gateway example; replace its local square-error benchmark."""

import asyncio
import json
import py_compile
import sys
from pathlib import Path

from llm4ad.gateway.sdk import (
    EvaluationArtifact,
    EvaluationContract,
    EvaluationError,
    EvaluationResult,
    GatewayEvaluator,
    Metric,
    MetricType,
    RemoteJob,
    Reporter,
)


class Evaluator(GatewayEvaluator):
    """Own only compilation, local execution and score aggregation."""

    contract = EvaluationContract(
        evaluator_id="square-error-example",
        version="1",
        data_version="1",
        metrics=[Metric(name="mse", type=MetricType.MINIMIZE)],
    )

    async def before_task(self, job: RemoteJob, reporter: Reporter, operation: str) -> None:
        """Check syntax before evaluation or local replay, without preparing cancellations."""
        if operation == "cancel":
            return
        candidate = Path(job.project_root) / "solution.py"
        reporter.metadata({"benchmark_version": "square-error-v1"})
        reporter.message("Using the fixed local benchmark cases", data={"candidate": candidate.name})
        reporter.progress("Checking candidate syntax", completed=0, total=2)
        py_compile.compile(str(candidate), doraise=True)

    @staticmethod
    def diagnostic_path(job: RemoteJob, reporter: Reporter, name: str) -> Path:
        """Use SDK private logs, retaining compatibility with simple standalone reporters."""
        local_log = getattr(reporter, "local_log", None)
        target = local_log(name, description="Basic evaluator diagnostics") if callable(local_log) else None
        path = Path(target) if isinstance(target, (str, Path)) else Path(job.artifact_dir) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    async def evaluate(self, job: RemoteJob, reporter: Reporter) -> EvaluationResult:
        """Execute the prepared candidate and return measured scores.

        Args:
            job: SDK-owned candidate workspace, execution deadline and parameters.
            reporter: Durable progress, external recovery state and evidence API.

        Returns:
            Aggregated score, measured metrics, JSON metadata and declared files.
        """
        candidate = Path(job.project_root) / "solution.py"
        # Replace this block with your compiler, device runner or scheduler.
        # Scheduling, interruption recovery and cancellation use SDK defaults.
        runner = Path(__file__).with_name("runner.py")
        reporter.progress("Running local evaluation", completed=1, total=2)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(runner),
            str(candidate),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        with self.diagnostic_path(job, reporter, "runner.stdout").open("ab") as output:
            output.write(stdout)
        with self.diagnostic_path(job, reporter, "runner.stderr").open("ab") as output:
            output.write(stderr)
        if process.returncode:
            raise EvaluationError(
                stderr.decode(errors="replace") or "Candidate runner failed",
                metadata={"stage": "execution", "returncode": process.returncode},
            )
        measured = json.loads(stdout)
        return EvaluationResult(
            score=-measured["mse"],
            metrics={"mse": measured["mse"]},
            metadata={
                "cases": len(measured["cases"]),
                "evaluation_profile": job.evaluation_profile,
                "benchmark": "Explicit square-error example; replace for your project",
                "measurements": measured,
            },
        )

    async def after_task(self, job: RemoteJob, reporter: Reporter, operation: str, *, result=None, error=None) -> None:
        """Finalize outputs and diagnostics after preparation or execution, preserving errors."""
        if operation == "cancel":
            return
        if error is not None:
            with self.diagnostic_path(job, reporter, "operation-error.log").open("a", encoding="utf-8") as output:
                output.write(str(error) + "\n")
            if isinstance(error, EvaluationError) and error.metadata.get("stage") == "execution":
                output = Path(job.artifact_dir)
                output.mkdir(exist_ok=True)
                stderr = self.diagnostic_path(job, reporter, "runner.stderr").read_bytes()
                (output / "runner-stderr.txt").write_bytes(stderr or b"Runner exited without stderr")
                error.artifacts.append(
                    EvaluationArtifact(name="runner-stderr.txt", path="runner-stderr.txt", description="Runner stderr")
                )
            return
        if not isinstance(result, EvaluationResult):
            return
        measured = result.metadata["measurements"]
        output = Path(job.artifact_dir)
        output.mkdir(exist_ok=True)
        (output / "measurements.json").write_text(json.dumps(measured, indent=2), encoding="utf-8")
        (output / "report.md").write_text(f"# Local evaluation\n\nMean squared error: **{measured['mse']}**.\n", encoding="utf-8")
        result.artifacts.extend(
            [
                EvaluationArtifact(
                    name="measurements.json",
                    path="measurements.json",
                    media_type="application/json",
                    description="Per-case measurements from the fixed benchmark",
                ),
                EvaluationArtifact(
                    name="report.md", path="report.md", media_type="text/markdown", description="Readable evaluation summary"
                ),
            ]
        )
        reporter.progress("Evaluation finished", completed=2, total=2)
