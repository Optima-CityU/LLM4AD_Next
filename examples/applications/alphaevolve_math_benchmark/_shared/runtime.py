"""Shared process and JSON contract for the mathematics benchmark cases."""

from __future__ import annotations

import asyncio
import json
import math
import sys
import time
from abc import abstractmethod
from pathlib import Path
from typing import Any, Literal

import numpy as np

from llm4ad.evaluator.base import BaseEvaluator, EvalContext, EvaluationResult, Metric, MetricType

TOL = 1e-9


class JsonBenchmarkEvaluator(BaseEvaluator):
    """Execute a JSON-emitting candidate and expose its original objective."""

    metric_name = "objective"
    metric_type = MetricType.MAXIMIZE
    registry_key = "json_math_benchmark"
    benchmark_key = "math_benchmark"
    target_value: float | None = None
    score_mode: Literal["target_over_objective", "objective_over_target"] | None = None
    target_ratio_metric_name: str | None = "target_ratio"
    target_value_metric_name: str | None = None

    def __init__(self) -> None:
        objective_weight = 0.0 if self.score_mode is not None else 1.0
        self._metrics = [Metric(name=self.metric_name, type=self.metric_type, weight=objective_weight)]
        if self.target_ratio_metric_name is not None and self.score_mode is not None:
            self._metrics.append(
                Metric(name=self.target_ratio_metric_name, type=MetricType.MAXIMIZE, weight=1.0)
            )
        if self.target_value_metric_name is not None:
            self._metrics.append(
                Metric(name=self.target_value_metric_name, type=MetricType.MAXIMIZE, weight=0.0)
            )
        self._metrics.extend(
            [
                Metric(name="validity", type=MetricType.MAXIMIZE, weight=0.0),
                Metric(name="eval_time", type=MetricType.MINIMIZE, weight=0.0),
            ]
        )

    @property
    def name(self) -> str:
        return self.registry_key

    @property
    def metrics(self) -> list[Metric]:
        return self._metrics

    def _failure(self, message: str, started_at: float) -> EvaluationResult:
        return EvaluationResult(
            score=0.0,
            metrics={"validity": 0.0},
            monitor_metrics={"validity": 0.0},
            success=False,
            error_message=message,
            duration_ms=(time.monotonic() - started_at) * 1000,
        )

    @abstractmethod
    def measure(self, payload: dict[str, Any]) -> float:
        """Validate the candidate payload and return its raw objective."""

    async def evaluate(self, cfg: EvalContext) -> EvaluationResult:
        started_at = time.monotonic()
        solve_path = Path(cfg.project_root) / "solve.py"
        if not solve_path.is_file():
            return self._failure(f"solve.py not found in {cfg.project_root}", started_at)

        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(solve_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(solve_path.parent),
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=cfg.timeout)
            except TimeoutError:
                process.kill()
                await process.communicate()
                return self._failure(f"Evaluation timed out after {cfg.timeout:g}s", started_at)
            if process.returncode != 0:
                detail = stderr.decode(errors="replace").strip()
                return self._failure(f"Candidate execution failed: {detail}", started_at)

            lines = [line.strip() for line in stdout.decode(errors="replace").splitlines() if line.strip()]
            if not lines:
                raise ValueError("candidate produced no output")
            payload = json.loads(lines[-1])
            if not isinstance(payload, dict):
                raise ValueError("candidate output must be a JSON object")
            objective = float(self.measure(payload))
            if not math.isfinite(objective):
                raise ValueError("objective is not finite")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            return self._failure(f"Validation failed: {exc}", started_at)
        except Exception as exc:
            return self._failure(f"Evaluation failed: {exc}", started_at)

        elapsed_seconds = time.monotonic() - started_at
        if self.score_mode is None:
            score = objective if self.metric_type == MetricType.MAXIMIZE else -objective
        else:
            if self.target_value is None or self.target_value <= 0 or objective <= 0:
                return self._failure("target-normalized objectives must be positive", started_at)
            score = (
                self.target_value / objective
                if self.score_mode == "target_over_objective"
                else objective / self.target_value
            )

        metrics = {
            self.metric_name: objective,
            "validity": 1.0,
            "eval_time": elapsed_seconds,
        }
        if self.target_ratio_metric_name is not None and self.score_mode is not None:
            metrics[self.target_ratio_metric_name] = score
        if self.target_value_metric_name is not None and self.target_value is not None:
            metrics[self.target_value_metric_name] = self.target_value
        return EvaluationResult(
            score=score,
            metrics=metrics,
            monitor_metrics=metrics,
            success=True,
            duration_ms=elapsed_seconds * 1000,
            metadata={
                "benchmark": self.benchmark_key,
                "objective_direction": self.metric_type.value,
                "raw_objective": objective,
            },
        )


def finite_array(
    payload: dict[str, Any],
    key: str,
    shape: tuple[int, ...] | None = None,
) -> np.ndarray:
    """Read one finite float array from a candidate payload."""
    array = np.asarray(payload[key], dtype=float)
    if shape is not None and array.shape != shape:
        raise ValueError(f"{key} must have shape {shape}, got {array.shape}")
    if not np.isfinite(array).all():
        raise ValueError(f"{key} must contain only finite values")
    return array
