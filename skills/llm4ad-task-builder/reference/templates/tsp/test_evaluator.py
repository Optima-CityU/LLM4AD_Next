"""Test that the TSP evaluator loads correctly via the dispatcher.

This test verifies the evaluator wiring without requiring LLM credentials.
"""

from llm4ad.config import CustomEvaluatorConfig
from llm4ad.evaluator.dispatcher import EvaluationDispatcher


def test_evaluator_loads():
    """Test that the evaluator class loads through the registry."""
    config = CustomEvaluatorConfig(
        type="custom",
        module="tsp_evaluator.py:PythonTSPEvaluator",
        timeout=60.0,
        dataset={"mode": "directory", "path": "data/sample", "recursive": False},
        metrics=["tour_length", "execution_time_ms", "valid_tour"],
    )

    dispatcher = EvaluationDispatcher(config=config)
    print(f"[OK] Evaluator class loaded: {dispatcher._eval_cls}")
    print(f"[OK] Metric definitions: {list(dispatcher.get_metric_definitions().keys())}")
    print("[OK] Test passed: evaluator loads correctly")


if __name__ == "__main__":
    test_evaluator_loads()
