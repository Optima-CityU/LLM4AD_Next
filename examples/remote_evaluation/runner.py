"""Run the example candidate against evaluator-local inputs."""

import importlib.util
import json
import sys


def main():
    """Load solve(x), evaluate local cases and emit one JSON result."""
    spec = importlib.util.spec_from_file_location("candidate", sys.argv[1])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    inputs = [-2.0, -1.0, 0.0, 1.0, 2.0]
    mse = sum((float(module.solve(x)) - x * x) ** 2 for x in inputs) / len(inputs)
    print(json.dumps({"mse": mse}, allow_nan=False))


if __name__ == "__main__":
    main()
