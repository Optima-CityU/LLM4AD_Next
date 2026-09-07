#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """Generate optimized step function using refined boundary-focused approach."""
    dimension = 600
    interval_start = -1 / 4
    interval_end = 1 / 4

    x = np.linspace(interval_start, interval_end, dimension)
    step_function = 1.0 + 4.0 * np.abs(x) - 16.0 * x**2
    step_function = (step_function + step_function[::-1]) / 2
    step_function = np.maximum(step_function, 0)
    step_function /= np.sum(step_function)

    return step_function
# EVOLVE_END


def evaluate_sequence(sequence: list[float]) -> float:
    if not isinstance(sequence, list) or not sequence:
        return np.inf
    for value in sequence:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return np.inf
        if np.isnan(value) or np.isinf(value):
            return np.inf
    sequence = [min(1000.0, max(0.0, float(value))) for value in sequence]
    total = np.sum(sequence)
    if total < 0.01:
        return np.inf
    convolution = np.convolve(sequence, sequence)
    return float(2 * len(sequence) * max(convolution) / total**2)


def run_search_for_best_sequence():
    return list(search_for_best_sequence())


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))
