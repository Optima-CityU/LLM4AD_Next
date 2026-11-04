"""Evaluator for the second autocorrelation inequality."""

import math
from typing import Any

import numpy as np
from _shared.runtime import JsonBenchmarkEvaluator, finite_array

from llm4ad.evaluator.base import BaseEvaluator


def _lower_bound(heights: np.ndarray) -> float:
    convolution = np.convolve(heights, heights)
    widths = np.diff(np.linspace(-0.5, 0.5, len(convolution) + 2))
    values = np.concatenate(([0.0], convolution, [0.0]))
    l2_squared = sum(
        widths[index] / 3.0 * (values[index] ** 2 + values[index] * values[index + 1] + values[index + 1] ** 2)
        for index in range(len(convolution) + 1)
    )
    l1 = float(np.sum(np.abs(convolution)) / (len(convolution) + 1))
    linf = float(np.max(np.abs(convolution)))
    if l1 <= 0 or linf <= 0:
        raise ValueError("heights must define non-zero convolution norms")
    return float(l2_squared / (l1 * linf))


@BaseEvaluator.register("alphaevolve_second_autocorrelation_evaluator")
class SecondAutocorrelationEvaluator(JsonBenchmarkEvaluator):
    metric_name = "c_lower_bound"
    target_value = 0.8963
    score_mode = "objective_over_target"
    registry_key = "alphaevolve_second_autocorrelation_evaluator"
    benchmark_key = "second_autocorrelation_inequality"

    def measure(self, payload: dict[str, Any]) -> float:
        heights = finite_array(payload, 'heights', (50,))
        if np.any(heights < 0):
            raise ValueError('heights must be non-negative')
        scale = float(np.max(heights))
        if scale <= 0:
            raise ValueError('heights must define non-zero convolution norms')
        # The norm ratio is homogeneous; rescaling avoids overflow/underflow
        # without changing the mathematical construction or objective.
        computed = _lower_bound(heights / scale)
        reported = float(payload['c_lower_bound'])
        if not math.isfinite(reported) or not math.isclose(reported, computed, rel_tol=1e-12, abs_tol=1e-15):
            raise ValueError(f'c_lower_bound miscalculation: expected {computed}')
        return computed
