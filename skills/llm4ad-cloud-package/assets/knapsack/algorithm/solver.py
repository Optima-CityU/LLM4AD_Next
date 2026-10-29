"""Greedy baseline for a JSON-CLI 0/1 knapsack task."""

import json
import sys


# EVOLVE_START
def solve(instance: dict) -> dict:
    """Select items by decreasing value-to-weight ratio.

    Args:
        instance: Capacity and a list of positive-weight, nonnegative-value items.

    Returns:
        Selected unique item indices.
    """
    items = instance["items"]
    order = sorted(
        range(len(items)),
        key=lambda index: items[index]["value"] / items[index]["weight"],
        reverse=True,
    )
    remaining = instance["capacity"]
    selected = []
    for index in order:
        if items[index]["weight"] <= remaining:
            selected.append(index)
            remaining -= items[index]["weight"]
    return {"selected": selected}


# EVOLVE_END


if __name__ == "__main__":
    print(json.dumps(solve(json.loads(sys.argv[1]))))
