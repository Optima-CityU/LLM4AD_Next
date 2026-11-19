"""Small deterministic evaluator demonstrating explicit report retention."""

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from llm4ad.config.evaluator import CustomEvaluatorConfig, EvalContext
from llm4ad.evaluator import BaseEvaluator, EvaluationArtifact, EvaluationDispatcher, EvaluationResult, Metric, MetricType


class ReportEvaluator(BaseEvaluator):
    """Evaluate square(x) and retain a JSON report and Markdown explanation."""

    @property
    def name(self) -> str:
        """Return the evaluator name."""
        return "explicit-reports-example"

    @property
    def metrics(self) -> list[Metric]:
        """Declare the actual error objective."""
        return [Metric(name="mse", type=MetricType.MINIMIZE)]

    async def evaluate(self, cfg: EvalContext) -> EvaluationResult:
        """Run the candidate on fixed inputs and emit explicitly declared files."""
        spec = importlib.util.spec_from_file_location("report_candidate", Path(cfg.project_root) / "solution.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cases = [
            {"input": value, "expected": value * value, "actual": float(module.square(value))} for value in (-2, -1, 0, 1, 2)
        ]
        mse = sum((case["actual"] - case["expected"]) ** 2 for case in cases) / len(cases)
        output = Path(cfg.artifact_dir)
        (output / "measurements.json").write_text(json.dumps(cases, indent=2), encoding="utf-8")
        (output / "report.md").write_text(
            f"# Candidate evaluation\n\nEvaluated {len(cases)} fixed inputs. Mean squared error: **{mse}**.\n",
            encoding="utf-8",
        )
        return EvaluationResult(
            score=-mse,
            metrics={"mse": mse},
            metadata={"inputs": len(cases), "method": "Fixed deterministic cases"},
            artifacts=[
                EvaluationArtifact(name="reports/measurements.json", path="measurements.json", media_type="application/json"),
                EvaluationArtifact(name="reports/report.md", path="report.md", media_type="text/markdown"),
            ],
        )


async def main():
    """Exercise the same dispatcher and retention path without calling a model."""
    root = Path(__file__).resolve().parent
    dispatcher = EvaluationDispatcher(
        CustomEvaluatorConfig(module=f"{__file__}:ReportEvaluator"),
        records_dir=root / ".evaluation-output",
    )
    candidate = SimpleNamespace(
        id="reportdemo01",
        name="Square baseline",
        worktree=SimpleNamespace(path=str(root)),
        custom_metadata={},
        code_fingerprint=lambda: hashlib.sha256((root / "solution.py").read_bytes()).hexdigest(),
    )
    result = (await dispatcher.dispatch_batch([candidate]))[0]
    print(json.dumps({"score": result.score, "records": result.metadata["evaluation_records"]}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
