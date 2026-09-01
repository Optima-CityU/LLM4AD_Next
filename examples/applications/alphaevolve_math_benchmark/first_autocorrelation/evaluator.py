"""Evaluator for the first autocorrelation inequality."""

from typing import Any

import numpy as np
from _shared.runtime import JsonBenchmarkEvaluator

from llm4ad.config.schema import EvalContext
from llm4ad.evaluator.base import BaseEvaluator, EvaluationResult, Metric, MetricType

TARGET_VALUE = 1.5053


@BaseEvaluator.register("alphaevolve_first_autocorrelation_evaluator")
class FirstAutocorrelationEvaluator(JsonBenchmarkEvaluator):
    metric_name = "upper_bound"
    metric_type = MetricType.MINIMIZE
    registry_key = "alphaevolve_first_autocorrelation_evaluator"
    benchmark_key = "first_autocorrelation_inequality"

    def __init__(self) -> None:
        super().__init__()
        self._metrics = [
            Metric(name="target_ratio", type=MetricType.MAXIMIZE, weight=1.0),
            Metric(name="upper_bound", type=MetricType.MINIMIZE, weight=0.0),
            Metric(name="validity", type=MetricType.MAXIMIZE, weight=0.0),
            Metric(name="eval_time", type=MetricType.MINIMIZE, weight=0.0),
        ]

    def measure(self, payload: dict[str, Any]) -> float:
        raw_sequence = payload["sequence"]
        if not isinstance(raw_sequence, list) or not raw_sequence:
            raise ValueError("sequence must be a non-empty list")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in raw_sequence):
            raise ValueError("sequence values must be integers or floats")
        sequence = np.asarray(raw_sequence, dtype=float)
        if not np.isfinite(sequence).all():
            raise ValueError("sequence values must be finite")
        sequence = np.clip(sequence, 0.0, 1000.0)
        total = float(np.sum(sequence))
        if total < 0.01:
            raise ValueError("sequence sum must exceed 0.01")
        convolution = np.convolve(sequence, sequence)
        return float(2 * len(sequence) * np.max(convolution) / total**2)

    async def evaluate(self, cfg: EvalContext) -> EvaluationResult:
        result = await super().evaluate(cfg)
        if not result.success:
            return result

        upper_bound = result.metrics["upper_bound"]
        target_ratio = TARGET_VALUE / upper_bound
        metrics = {
            "upper_bound": upper_bound,
            "target_ratio": target_ratio,
            "validity": 1.0,
            "eval_time": result.duration_ms / 1000.0,
        }
        return result.model_copy(
            update={
                "score": target_ratio,
                "metrics": metrics,
                "monitor_metrics": metrics,
                "metadata": {
                    **result.metadata,
                    "objective_direction": MetricType.MAXIMIZE.value,
                    "raw_objective": upper_bound,
                    "target_value": TARGET_VALUE,
                },
            }
        )
