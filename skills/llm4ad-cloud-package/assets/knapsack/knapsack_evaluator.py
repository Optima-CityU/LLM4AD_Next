"""Evaluate the supplied knapsack candidate rather than a fixed seed file."""

import asyncio
import json
import math
import sys
from pathlib import Path

from llm4ad.evaluator.base import (
    BaseEvaluator,
    EvalContext,
    EvaluationResult,
    Metric,
    MetricType,
)


@BaseEvaluator.register("cloud_knapsack")
class KnapsackEvaluator(BaseEvaluator):
    """Score feasible selections using independently computed item values."""

    @property
    def name(self) -> str:
        """Return the registry name."""
        return "cloud_knapsack"

    @property
    def metrics(self) -> list[Metric]:
        """Declare the objective and a weight diagnostic."""
        return [
            Metric(name="total_value", type=MetricType.MAXIMIZE),
            Metric(name="total_weight", type=MetricType.MINIMIZE, weight=0),
        ]

    async def evaluate(self, cfg: EvalContext) -> EvaluationResult:
        """Run and validate one candidate on one instance.

        Args:
            cfg: Candidate directory, instance file, and execution timeout.

        Returns:
            Finite feasible score or an explicit evaluation failure.
        """
        process = None
        try:
            script = Path(cfg.project_root) / "solver.py"
            if not script.is_file():
                raise ValueError("Candidate solver.py is missing")
            instance = json.loads(Path(cfg.data_path).read_text(encoding="utf-8"))
            if not isinstance(instance, dict):
                raise ValueError("Instance must be an object with capacity and items")
            items = instance["items"]
            capacity = instance["capacity"]
            if type(capacity) not in (int, float) or not math.isfinite(capacity) or capacity < 0:
                raise ValueError("Capacity must be a finite nonnegative number")
            if not isinstance(items, list) or not items:
                raise ValueError("Items must be a nonempty array")
            for item in items:
                if not isinstance(item, dict) or not all(
                    type(item.get(key)) in (int, float) and math.isfinite(item[key]) for key in ("weight", "value")
                ):
                    raise ValueError("Item weight and value must be finite numbers")
                if item["weight"] <= 0 or item["value"] < 0:
                    raise ValueError("Items require positive weights and nonnegative values")
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(script),
                json.dumps(instance),
                cwd=cfg.project_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), cfg.timeout)
            if process.returncode:
                raise ValueError(f"Candidate exited with {process.returncode}: {stderr.decode(errors='replace')}")
            selected = json.loads(stdout)["selected"]
            if not isinstance(selected, list) or any(type(index) is not int for index in selected):
                raise ValueError("selected must be a list of integer indices")
            if len(set(selected)) != len(selected):
                raise ValueError("Duplicate item indices")
            if any(index < 0 or index >= len(items) for index in selected):
                raise ValueError("Item index outside the instance")
            weight = sum(items[index]["weight"] for index in selected)
            value = sum(items[index]["value"] for index in selected)
            if weight > capacity:
                raise ValueError("Knapsack capacity exceeded")
            if not math.isfinite(weight) or not math.isfinite(value):
                raise ValueError("Nonfinite candidate result")
            return EvaluationResult(
                score=float(value),
                metrics={"total_value": float(value), "total_weight": float(weight)},
            )
        except TimeoutError:
            return EvaluationResult(metrics={}, success=False, error_message=f"Candidate timed out after {cfg.timeout:g} seconds")
        except Exception as exc:
            return EvaluationResult(metrics={}, success=False, error_message=str(exc))
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.communicate()
