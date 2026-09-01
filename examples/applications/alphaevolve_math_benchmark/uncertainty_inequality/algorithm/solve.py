#!/usr/bin/env python3
"""Initial Hermite coefficients for the uncertainty inequality."""

import json


# EVOLVE_START
def find_coefficients():
    return [1.0, 2.0, 3.0]
# EVOLVE_END


if __name__ == "__main__":
    print(json.dumps({"coefficients": find_coefficients()}))
