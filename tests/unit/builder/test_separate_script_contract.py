"""Contract test: evaluator, algorithm main(), and validator agree.

Proves the single separate-script contract end to end WITHOUT an API key:
a hand-authored algorithm file (main() reads the full instance dict from
sys.argv[1]) plus a hand-authored evaluator (spawns the algorithm as a
subprocess) pass every TaskValidator stage. Also covers the deterministic
boilerplate renderers and the derived class-name helper.
"""

from __future__ import annotations

import ast
import json

import pytest

from llm4ad.builder.blueprint import AnalysisResult, TaskBlueprint
from llm4ad.builder.creator import TaskCreator
from llm4ad.builder.validator import TaskValidator

pytestmark = pytest.mark.unit


# --- A minimal, self-contained separate-script algorithm -------------------
_ALGORITHM_CODE = '''\
import json
import sys

# EVOLVE_START
def double_values(numbers):
    """Baseline: double each value."""
    return [n * 2 for n in numbers]
# EVOLVE_END


def process(data):
    """Wrapper that calls the evolvable function and formats the result."""
    numbers = data["numbers"]
    result = double_values(numbers)
    return {"result": result, "primary_score": float(sum(result))}


def main():
    if len(sys.argv) < 2:
        print("Usage: python doubler.py '<input_json>'", file=sys.stderr)
        sys.exit(1)
    input_data = json.loads(sys.argv[1])
    print(json.dumps(process(input_data)))


if __name__ == "__main__":
    main()
'''


def _make_analysis() -> AnalysisResult:
    """Build a minimal AnalysisResult for the doubler task."""
    return AnalysisResult(
        problem_type="other",
        evaluation_pattern="subprocess",
        function_name="double_values",
        function_signature="def double_values(numbers: list) -> list:",
        function_description="Double every value in the list.",
        metrics=[
            {"name": "primary_score", "type": "maximize", "weight": 1.0,
             "description": "Sum of doubled values"},
            {"name": "execution_time_ms", "type": "minimize", "weight": 0.1,
             "description": "Execution time"},
        ],
        input_format='{"numbers": [int, ...]}',
        output_format='{"result": [int, ...], "primary_score": float}',
        algorithm_dir_name="doubler_algorithm",
        algorithm_file_name="doubler.py",
        project_name="doubler_task",
        background="Double a list of numbers.",
    )


def _build_evaluator_code(class_name: str, register_name: str,
                          algo_dir: str, algo_file: str) -> str:
    """Hand-author a separate-script evaluator with names baked in."""
    return f'''\
import asyncio
import json
import sys
import time
from pathlib import Path

from llm4ad.evaluator.base import (
    BaseEvaluator,
    EvalContext,
    EvaluationResult,
    Metric,
    MetricType,
)


@BaseEvaluator.register("{register_name}")
class {class_name}(BaseEvaluator):
    """Doubler evaluator (separate-script)."""

    def __init__(self):
        self._metrics = [
            Metric(name="primary_score", type=MetricType.MAXIMIZE, weight=1.0,
                   description="Sum of doubled values"),
            Metric(name="execution_time_ms", type=MetricType.MINIMIZE, weight=0.1,
                   description="Execution time"),
        ]

    @property
    def name(self) -> str:
        return "{register_name}"

    @property
    def metrics(self) -> list:
        return self._metrics

    @staticmethod
    def _resolve_algorithm_file(project_root: Path):
        nested = project_root / "{algo_dir}" / "{algo_file}"
        if nested.exists():
            return nested
        flat = project_root / "{algo_file}"
        if flat.exists():
            return flat
        return None

    async def evaluate(self, cfg: EvalContext) -> EvaluationResult:
        start_time = time.time()
        project_root = Path(cfg.project_root)
        data_path = Path(cfg.data_path)
        if not data_path.exists():
            return EvaluationResult(score=0.0, metrics={{}}, success=False,
                                    error_message="Data file not found",
                                    duration_ms=0.0)
        algo_file = self._resolve_algorithm_file(project_root)
        if algo_file is None:
            return EvaluationResult(score=0.0, metrics={{}}, success=False,
                                    error_message="Algorithm not found",
                                    duration_ms=0.0)
        instance_json = data_path.read_text(encoding="utf-8").strip()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(algo_file), instance_json,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=cfg.timeout)
        duration_ms = (time.time() - start_time) * 1000
        if proc.returncode != 0:
            return EvaluationResult(
                score=0.0, metrics={{}}, success=False,
                error_message=stderr_bytes.decode("utf-8", "replace")[:500],
                duration_ms=duration_ms)
        result = json.loads(stdout_bytes.decode("utf-8", "replace").strip())
        if isinstance(result, dict) and "error" in result:
            return EvaluationResult(score=0.0, metrics={{}}, success=False,
                                    error_message=str(result["error"]),
                                    duration_ms=duration_ms)
        score = float(result.get("primary_score", 0.0))
        return EvaluationResult(
            score=score,
            metrics={{"primary_score": score, "execution_time_ms": duration_ms}},
            success=True, duration_ms=duration_ms)
'''


def _make_blueprint() -> TaskBlueprint:
    """Assemble a full blueprint using the deterministic creator helpers."""
    analysis = _make_analysis()
    class_name = TaskCreator._derive_evaluator_class_name(analysis)
    register_name = analysis.project_name.replace("-", "_") + "_evaluator"
    evaluator_file_name = register_name + ".py"

    evaluator_code = _build_evaluator_code(
        class_name, register_name,
        analysis.algorithm_dir_name, analysis.algorithm_file_name,
    )
    debug_run_code = TaskCreator._render_debug_run(analysis)
    test_evaluator_code = TaskCreator._render_test_evaluator(
        analysis, evaluator_file_name, class_name, multimodal=False,
    )
    config_yaml = TaskCreator(provider=None)._build_config_yaml(  # type: ignore[arg-type]
        analysis, class_name, evaluator_file_name, multimodal=False,
    )

    return TaskBlueprint(
        project_name=analysis.project_name,
        task_description=analysis.background,
        evaluator_code=evaluator_code,
        algorithm_code=_ALGORITHM_CODE,
        config_yaml=config_yaml,
        debug_run_code=debug_run_code,
        evaluator_class_name=class_name,
        evaluator_file_name=evaluator_file_name,
        algorithm_dir_name=analysis.algorithm_dir_name,
        algorithm_file_name=analysis.algorithm_file_name,
        function_to_evolve=analysis.function_name,
        metrics=analysis.metrics,
        dataset_files={"data/sample/instance_001.json": json.dumps({"numbers": [1, 2, 3]})},
        test_evaluator_code=test_evaluator_code,
    )


def test_derive_evaluator_class_name():
    """Project slug maps to PascalCase + Evaluator."""
    analysis = _make_analysis()
    assert TaskCreator._derive_evaluator_class_name(analysis) == "DoublerTaskEvaluator"


def test_rendered_boilerplate_is_valid_python():
    """debug_run.py and test_evaluator.py render to parseable Python."""
    analysis = _make_analysis()
    debug = TaskCreator._render_debug_run(analysis)
    test = TaskCreator._render_test_evaluator(
        analysis, "doubler_task_evaluator.py", "DoublerTaskEvaluator", multimodal=False,
    )
    ast.parse(debug)
    ast.parse(test)
    # The algorithm dir/file names must be baked into debug_run.
    assert "doubler_algorithm" in debug
    assert "doubler.py" in debug
    # The evaluator module/class must be baked into the test.
    assert "from doubler_task_evaluator import DoublerTaskEvaluator" in test
    assert '"primary_score"' in test


def test_full_validation_gate_passes():
    """The hand-authored separate-script package passes every validator stage."""
    blueprint = _make_blueprint()
    validator = TaskValidator(provider=None)  # type: ignore[arg-type]
    result = validator.check(blueprint, multimodal=False, skip_debug_run=False)
    assert result.validation_status == "passed", result.validation_errors
