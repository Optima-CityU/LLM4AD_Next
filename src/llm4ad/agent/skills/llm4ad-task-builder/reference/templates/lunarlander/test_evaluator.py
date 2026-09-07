"""Direct test for LunarLanderPolicyEvaluator.

This test demonstrates direct instantiation of a custom evaluator.
"""

import asyncio
from pathlib import Path

from llm4ad.evaluator.base import EvalContext


async def test_lunarlander_evaluator():
    """Test LunarLander evaluator with a sample episode configuration."""
    # Import evaluator from local module
    from lunarlander_evaluator import LunarLanderPolicyEvaluator

    evaluator = LunarLanderPolicyEvaluator()

    # Create test context
    task_dir = Path(__file__).resolve().parent
    ctx = EvalContext(
        project_root=str(task_dir),
        data_path=str(task_dir / "data" / "sample" / "instance_001.json"),
        timeout=60.0,
        behavior_storage="none",
    )

    # Run evaluation
    result = await evaluator.evaluate(ctx)

    print(f"Success: {result.success}")
    print(f"Score: {result.score}")
    print(f"Metrics: {result.metrics}")
    if result.error_message:
        print(f"Error: {result.error_message}")


if __name__ == "__main__":
    asyncio.run(test_lunarlander_evaluator())
