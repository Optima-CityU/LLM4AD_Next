"""Evaluator for the finite-set sums-and-differences problem."""

import math
from typing import Any

from _shared.runtime import JsonBenchmarkEvaluator

from llm4ad.evaluator.base import BaseEvaluator


@BaseEvaluator.register("alphaevolve_sums_differences_evaluator")
class SumsDifferencesEvaluator(JsonBenchmarkEvaluator):
    metric_name = "get_score_result"
    target_value = 1.1319033750264975
    score_mode = "objective_over_target"
    target_ratio_metric_name = None
    target_value_metric_name = "target_value"
    registry_key = "alphaevolve_sums_differences_evaluator"
    benchmark_key = "sums_and_differences"

    def measure(self, payload: dict[str, Any]) -> float:
        values = payload["values"]
        if not isinstance(values, list):
            raise ValueError("values must be a list")
        try:
            values = [int(value) for value in values]
        except (TypeError, ValueError) as exc:
            raise ValueError("values must contain integer-convertible entries") from exc
        if len(values) < 2:
            raise ValueError("values must contain at least two entries")
        unique = set(values)
        if len(unique) < 2:
            raise ValueError("values must contain at least two distinct integers")
        sums = {first + second for first in unique for second in unique}
        differences = {first - second for first in unique for second in unique}
        difference_ratio = len(differences) / len(unique)
        sum_ratio = len(sums) / len(unique)
        if difference_ratio <= 1 or sum_ratio <= 0:
            raise ValueError("set does not define a finite logarithmic ratio")
        return float(math.log(sum_ratio) / math.log(difference_ratio) + (1.0 - 1.0 / len(unique)) / 100.0)
