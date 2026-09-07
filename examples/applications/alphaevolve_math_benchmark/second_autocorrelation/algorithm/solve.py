#!/usr/bin/env python3
"""Initial step function for the second autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
def optimize_lower_bound():
    heights = [1.0] * 50
    convolution = np.convolve(heights, heights)
    widths = np.diff(np.linspace(-0.5, 0.5, len(convolution) + 2))
    values = np.concatenate(([0.0], convolution, [0.0]))
    l2_squared = sum(
        widths[index] / 3.0 * (values[index] ** 2 + values[index] * values[index + 1] + values[index + 1] ** 2)
        for index in range(len(convolution) + 1)
    )
    l1 = float(np.sum(np.abs(convolution)) / (len(convolution) + 1))
    linf = float(np.max(np.abs(convolution)))
    return heights, float(l2_squared / (l1 * linf))


# EVOLVE_END


if __name__ == "__main__":
    heights, c_lower_bound = optimize_lower_bound()
    print(json.dumps({"heights": heights, "c_lower_bound": c_lower_bound}))
