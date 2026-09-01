#!/usr/bin/env python3
"""Initial integer set for the sums-and-differences problem."""

import json

import numpy as np


# EVOLVE_START
def search_for_best_set():
    return np.arange(10), "initial consecutive-integer construction"
# EVOLVE_END


if __name__ == "__main__":
    values, _ = search_for_best_set()
    print(json.dumps({"values": values.tolist()}))
