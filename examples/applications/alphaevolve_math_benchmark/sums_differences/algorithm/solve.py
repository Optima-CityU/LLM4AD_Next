#!/usr/bin/env python3
"""Initial integer set for the sums-and-differences problem."""

import json
import math
from collections.abc import Iterable

import numpy as np


# EVOLVE_START
def search_for_best_set():
    """Return the initial finite integer set and a short strategy description."""
    return np.arange(10), "initial consecutive-integer construction"
# EVOLVE_END


def get_score(values: Iterable[object]) -> float:
    """Calculate the exact objective used by the benchmark evaluator.

    Args:
        values: Integer-convertible values in the candidate set.

    Returns:
        The logarithmic sums-and-differences score, including the finite-size
        correction used by the benchmark evaluator. Invalid sets return zero.
    """
    try:
        unique = {int(value) for value in values}
    except (TypeError, ValueError):
        return 0.0
    if len(unique) < 2:
        return 0.0

    sums = {first + second for first in unique for second in unique}
    differences = {first - second for first in unique for second in unique}
    difference_ratio = len(differences) / len(unique)
    sum_ratio = len(sums) / len(unique)
    if difference_ratio <= 1.0 or sum_ratio <= 0.0:
        return 0.0
    return float(
        math.log(sum_ratio) / math.log(difference_ratio)
        + (1.0 - 1.0 / len(unique)) / 100.0
    )


if __name__ == "__main__":
    values, _ = search_for_best_set()
    print(json.dumps({"values": values.tolist()}))
