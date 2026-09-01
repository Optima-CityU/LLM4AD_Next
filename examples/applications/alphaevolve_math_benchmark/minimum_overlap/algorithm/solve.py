#!/usr/bin/env python3
"""Initial relaxed sequence for the minimum-overlap problem."""

import json


# EVOLVE_START
def generate_erdos_data():
    return [0.5] * 50
# EVOLVE_END


if __name__ == "__main__":
    print(json.dumps({"half_sequence": generate_erdos_data()}))
