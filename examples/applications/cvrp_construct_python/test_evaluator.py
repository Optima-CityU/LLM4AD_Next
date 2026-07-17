"""Direct test for the CVRP evaluator (no LLM required).

Runs the baseline ``solve.py`` in ``cvrp_algorithm/`` against the generated
instances and checks that the evaluator returns a valid, finite result. Since
the baseline heuristic matches the instance ``label``, the normalized gap
should be approximately 0.

Usage:
    uv run python test_evaluator.py
"""

import asyncio
from pathlib import Path

# Import to trigger registration
import cvrp_evaluator  # noqa: F401

from llm4ad.config.schema import EvalContext
from llm4ad.evaluator.base import BaseEvaluator

HERE = Path(__file__).parent


async def main():
    """Evaluate the baseline solver against all generated instances."""
    evaluator = BaseEvaluator.create("cvrp_construct_evaluator")
    print(f"Evaluator: {evaluator.name}")
    print(f"Metrics: {[m.name for m in evaluator.metrics]}")

    data_dir = HERE / "data" / "instances"
    instances = sorted(data_dir.glob("*.json"))
    if not instances:
        print("No instances found. Run: uv run python generate_data.py first.")
        return

    project_root = str(HERE)  # solve.py is under cvrp_algorithm/ here

    for inst in instances:
        cfg = EvalContext(
            project_root=project_root,
            data_path=str(inst),
            timeout=30.0,
        )
        result = await evaluator.evaluate(cfg)
        status = "OK" if result.success else "FAIL"
        print(
            f"[{status}] {inst.name}: score={result.score:.4f} "
            f"cost={result.metrics.get('total_cost', float('nan')):.2f} "
            f"gap={result.metrics.get('normalized_gap', float('nan')):.4f}"
            + ("" if result.success else f" err={result.error_message}")
        )


if __name__ == "__main__":
    asyncio.run(main())
