"""Evaluator for the relaxed minimum-overlap sequence problem."""

from typing import Any

import numpy as np
from _shared.runtime import JsonBenchmarkEvaluator, finite_array

from llm4ad.evaluator.base import BaseEvaluator, MetricType


@BaseEvaluator.register("alphaevolve_minimum_overlap_evaluator")
class MinimumOverlapEvaluator(JsonBenchmarkEvaluator):
    metric_name = "upper bound"
    metric_type = MetricType.MINIMIZE
    target_value = 0.380927
    score_mode = "target_over_objective"
    target_ratio_metric_name = None
    target_value_metric_name = "target_upper_bound"
    registry_key = "alphaevolve_minimum_overlap_evaluator"
    benchmark_key = "minimum_overlap"

    def measure(self, payload: dict[str, Any]) -> float:
        half = finite_array(payload, "half_sequence")
        if half.ndim != 1 or len(half) < 2:
            raise ValueError("half_sequence must be a one-dimensional sequence")
        sequence = np.concatenate((half[:-1], half[::-1]))
        if np.any((sequence < 0) | (sequence > 1)):
            raise ValueError("sequence values must lie in [0, 1]")
        if not np.isclose(np.sum(sequence), len(sequence) / 2.0, rtol=1e-6):
            raise ValueError("sequence sum must equal half its length")
        overlap = np.correlate(sequence, 1 - sequence, mode="full")
        return float(np.max(overlap) / len(sequence) * 2.0)
