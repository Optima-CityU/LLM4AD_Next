"""Run the real baseline on every sample without a model call."""

import asyncio
import json
import tempfile
from pathlib import Path

from knapsack_evaluator import KnapsackEvaluator

from llm4ad.evaluator.base import EvalContext


async def check_evaluator() -> None:
    """Run the baseline, known scoring cases and actual failing candidates."""
    root = Path(__file__).resolve().parent
    evaluator = KnapsackEvaluator()
    instances = sorted((root / "data/sample").glob("*.json"))
    expected_values = [44.0, 79.0]
    for instance, expected in zip(instances, expected_values, strict=True):
        result = await evaluator.evaluate(
            EvalContext(
                project_root=str(root / "algorithm"),
                data_path=str(instance),
                timeout=10,
            )
        )
        assert result.success, result.error_message
        assert result.score == expected, (instance.name, result)
        print(f"{instance.name}: {result.score}, {result.metrics}")
    with tempfile.TemporaryDirectory(prefix="knapsack-empty-") as empty:
        result = await evaluator.evaluate(
            EvalContext(
                project_root=empty,
                data_path=str(instances[0]),
                timeout=10,
            )
        )
        assert not result.success, "Missing candidate must fail"

    with tempfile.TemporaryDirectory(prefix="knapsack-contract-") as temporary:
        candidate = Path(temporary)
        script = candidate / "solver.py"
        cases = [
            ("better feasible selection", '{"selected": [0, 2]}', 46.0),
            ("weaker feasible selection", '{"selected": [1]}', 14.0),
            ("empty feasible selection", '{"selected": []}', 0.0),
            ("duplicate indices", '{"selected": [0, 0]}', None),
            ("out of range", '{"selected": [99]}', None),
            ("negative index", '{"selected": [-1]}', None),
            ("boolean index", '{"selected": [true]}', None),
            ("fractional index", '{"selected": [0.5]}', None),
            ("capacity violation", '{"selected": [0, 1, 2]}', None),
            ("malformed output", "not-json", None),
        ]
        for label, output, expected in cases:
            script.write_text(f"print({output!r})\n", encoding="utf-8")
            result = await evaluator.evaluate(EvalContext(project_root=str(candidate), data_path=str(instances[0]), timeout=5))
            if expected is None:
                assert not result.success and result.error_message, (label, result)
            else:
                assert result.success and result.score == expected, (label, result)
                assert result.metrics["total_value"] == expected, (label, result)

        for source, label in [
            ("def invalid(:\n", "syntax error"),
            ("raise RuntimeError('candidate crash')\n", "runtime error"),
        ]:
            script.write_text(source, encoding="utf-8")
            result = await evaluator.evaluate(EvalContext(project_root=str(candidate), data_path=str(instances[0]), timeout=5))
            assert not result.success and result.error_message, (label, result)

        script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        result = await asyncio.wait_for(
            evaluator.evaluate(EvalContext(project_root=str(candidate), data_path=str(instances[0]), timeout=0.2)),
            timeout=5,
        )
        assert not result.success and "timed out" in (result.error_message or ""), result

        script.write_text("print('{\"selected\": []}')\n", encoding="utf-8")
        invalid_instance = candidate / "invalid.json"
        invalid_instance.write_text(json.dumps({"capacity": True, "items": []}), encoding="utf-8")
        result = await evaluator.evaluate(EvalContext(project_root=str(candidate), data_path=str(invalid_instance), timeout=5))
        assert not result.success and result.error_message, result
    print("Passed candidate scoring, invalid-output, syntax/runtime and timeout checks")


if __name__ == "__main__":
    asyncio.run(check_evaluator())
