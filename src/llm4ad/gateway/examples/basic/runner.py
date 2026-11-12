"""Execute candidate code in a separate process using local evaluation cases."""

import contextlib
import importlib.util
import json
import sys
from pathlib import Path


def main():
    """Load the candidate and return actual per-case measurements as JSON."""
    spec = importlib.util.spec_from_file_location("candidate", Path(sys.argv[1]))
    module = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(sys.stderr):
        spec.loader.exec_module(module)
        cases = [{"input": x, "expected": x * x, "actual": float(module.solve(x))} for x in (-2.0, -1.0, 0.0, 1.0, 2.0)]
    mse = sum((case["actual"] - case["expected"]) ** 2 for case in cases) / len(cases)
    print(json.dumps({"mse": mse, "cases": cases}, allow_nan=False))


if __name__ == "__main__":
    main()
