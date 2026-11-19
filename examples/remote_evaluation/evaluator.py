"""Minimal local gateway adapter, with no platform credentials or local dataset upload."""

import asyncio
import json
import sys
from pathlib import Path

from llm4ad.evaluator.base import EvaluationResult, Metric, MetricType
from llm4ad.remote import GatewayEvaluator, RemoteJob
from llm4ad.remote.contracts import EvaluationContract


class Evaluator(GatewayEvaluator):
    """Score a candidate solve(x) on a small deterministic local benchmark."""

    contract = EvaluationContract(
        evaluator_id="square-error-example",
        version="1",
        data_version="1",
        metrics=[Metric(name="mse", type=MetricType.MINIMIZE)],
    )

    async def evaluate(self, job: RemoteJob, reporter) -> EvaluationResult:
        """Run candidate code in a subprocess and aggregate local test cases."""
        runner = Path(__file__).with_name("runner.py")
        candidate = Path(job.project_root) / "solution.py"
        reporter.progress("Evaluating local benchmark", completed=0, total=1)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(runner),
            str(candidate),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode:
            raise RuntimeError(stderr.decode(errors="replace"))
        value = float(json.loads(stdout)["mse"])
        reporter.progress("Local benchmark completed", completed=1, total=1)
        return EvaluationResult(score=-value, metrics={"mse": value})
