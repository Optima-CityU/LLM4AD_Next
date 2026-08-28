"""Regression tests for generate-then-validate checkpoints and the final pass.

Covers two behaviors added to the builder pipeline:

1. Per-artifact checkpoints (:class:`TaskValidator.checkpoint_*`) run the
   earliest meaningful check on a single artifact right after generation and
   repair it in place, using a mock provider to supply the fix.
2. The whole-blueprint :meth:`TaskValidator.validate` runs one final
   validation after the last repair, so a fix applied on the final attempt is
   reported as ``passed`` rather than ``failed`` (the former off-by-one).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from llm4ad.builder.blueprint import TaskBlueprint
from llm4ad.builder.validator import TaskValidator

pytestmark = pytest.mark.unit


# --- Minimal mock provider -------------------------------------------------
@dataclass
class _Result:
    """Stand-in for GenerationResult carrying only the text field used here."""

    text: str


class _QueueProvider:
    """Provider stub that returns queued texts in order for each generate()."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def generate(self, prompt: str, **kwargs) -> _Result:  # noqa: D401, ANN003
        """Pop and return the next queued response."""
        self.calls += 1
        if not self._responses:
            raise AssertionError("provider.generate called more times than expected")
        return _Result(text=self._responses.pop(0))


# A syntactically valid separate-script algorithm the checkpoint should accept.
_GOOD_ALGORITHM = '''\
import json
import sys

# EVOLVE_START
def solve(numbers):
    """Sum the values."""
    return sum(numbers)
# EVOLVE_END


def process(data):
    return {"result": solve(data["numbers"])}


def main():
    if len(sys.argv) < 2:
        sys.exit(1)
    print(json.dumps(process(json.loads(sys.argv[1]))))


if __name__ == "__main__":
    main()
'''

# Same shape but with a syntax error inside the EVOLVE block.
_BROKEN_ALGORITHM = _GOOD_ALGORITHM.replace(
    "    return sum(numbers)", "    return sum(numbers"
)


def _algorithm_blueprint() -> TaskBlueprint:
    """Build a minimal separate-script blueprint for algorithm checkpoints."""
    return TaskBlueprint(
        project_name="sum_task",
        task_description="",
        evaluator_code="pass\n",
        algorithm_code=_GOOD_ALGORITHM,
        config_yaml=(
            "project_name: sum_task\n"
            "evaluator:\n"
            "  module: sum_evaluator.py:SumEvaluator\n"
            "evolution:\n"
            "  type: island_ga\n"
        ),
        debug_run_code="pass\n",
        evaluator_class_name="SumEvaluator",
        evaluator_file_name="sum_evaluator.py",
        algorithm_dir_name="sum_algorithm",
        algorithm_file_name="sum.py",
        function_to_evolve="solve",
        metrics=[],
    )


@pytest.mark.asyncio
async def test_checkpoint_algorithm_repairs_syntax_error():
    """A syntax-broken algorithm is repaired in place by the checkpoint."""
    provider = _QueueProvider([_GOOD_ALGORITHM])
    validator = TaskValidator(provider)  # type: ignore[arg-type]

    fixed = await validator.checkpoint_algorithm(
        _BROKEN_ALGORITHM, blueprint=_algorithm_blueprint(), max_repairs=2
    )

    assert provider.calls == 1  # one repair was needed
    import ast

    ast.parse(fixed)  # repaired source parses
    assert "EVOLVE_START" in fixed and "EVOLVE_END" in fixed


@pytest.mark.asyncio
async def test_checkpoint_algorithm_no_repair_when_clean():
    """A clean algorithm passes the checkpoint without calling the provider."""
    provider = _QueueProvider([])  # any call would raise
    validator = TaskValidator(provider)  # type: ignore[arg-type]

    fixed = await validator.checkpoint_algorithm(
        _GOOD_ALGORITHM, blueprint=_algorithm_blueprint(), max_repairs=2
    )

    assert provider.calls == 0
    assert fixed == _GOOD_ALGORITHM


@pytest.mark.asyncio
async def test_checkpoint_sample_data_regenerates_none():
    """A ``NONE`` sample is regenerated into valid JSON via the dataset path."""
    provider = _QueueProvider(['{"numbers": [1, 2, 3]}'])
    validator = TaskValidator(provider)  # type: ignore[arg-type]

    fixed = await validator.checkpoint_sample_data(
        "NONE",
        algorithm_code=_GOOD_ALGORITHM,
        function_to_evolve="solve",
        project_name="sum_task",
        max_repairs=2,
    )

    assert provider.calls == 1
    import json

    assert json.loads(fixed) == {"numbers": [1, 2, 3]}


@pytest.mark.asyncio
async def test_checkpoint_sample_data_accepts_valid_json():
    """Valid sample JSON passes the checkpoint without provider calls."""
    provider = _QueueProvider([])
    validator = TaskValidator(provider)  # type: ignore[arg-type]

    fixed = await validator.checkpoint_sample_data(
        '{"numbers": [4, 5]}',
        algorithm_code=_GOOD_ALGORITHM,
        function_to_evolve="solve",
        project_name="sum_task",
        max_repairs=2,
    )

    assert provider.calls == 0
    assert fixed == '{"numbers": [4, 5]}'


def _valid_config() -> str:
    return (
        "project_name: fin\n"
        "evaluator:\n"
        "  module: fin_evaluator.py:FinEvaluator\n"
        "evolution:\n"
        "  type: island_ga\n"
    )


def _blueprint_with_broken_syntax() -> TaskBlueprint:
    """Blueprint whose evaluator has a syntax error (fails stage-1 syntax)."""
    return TaskBlueprint(
        project_name="fin",
        task_description="",
        evaluator_code="def broken(:\n    pass\n",  # syntax error
        algorithm_code="# EVOLVE_START\npass\n# EVOLVE_END\n",
        config_yaml=_valid_config(),
        debug_run_code="pass\n",
        evaluator_class_name="FinEvaluator",
        evaluator_file_name="fin_evaluator.py",
        algorithm_dir_name="fin_algorithm",
        algorithm_file_name="fin.py",
        function_to_evolve="solve",
        metrics=[],
        dataset_files={"data/sample/instance_001.json": "{}"},
        test_evaluator_code="pass\n",
    )


@pytest.mark.asyncio
async def test_validate_runs_final_pass_after_last_repair(monkeypatch):
    """A fix applied on the final attempt is reported passed, not failed.

    Drives ``validate`` with ``max_attempts=1``: attempt 1 sees the broken
    evaluator, repairs it, and the loop ends. The added final pass must then
    re-validate the repaired blueprint and mark it ``passed`` — the previous
    off-by-one would have left it ``failed``.
    """
    blueprint = _blueprint_with_broken_syntax()

    good_evaluator = (
        "class FinEvaluator:\n"
        "    pass\n"
    )

    # Repair replaces the evaluator with syntactically valid code.
    async def fake_repair(
        self,
        evaluator_code,
        error,
        *,
        history_section="",
        multimodal=False,
        evaluation_pattern="separate_script",
    ):
        return good_evaluator

    # Neutralize the runtime stages so the final pass hinges on syntax only:
    # after the syntax repair, all stages should report no error.
    def only_syntax_stages(self, bp, *, multimodal=False, skip_debug_run=False):
        return self._check_syntax("evaluator", bp.evaluator_code)

    monkeypatch.setattr(TaskValidator, "_repair_evaluator", fake_repair)
    monkeypatch.setattr(TaskValidator, "_run_validation_stages", only_syntax_stages)

    validator = TaskValidator(provider=object())  # type: ignore[arg-type]
    result = await validator.validate(blueprint, max_attempts=1, skip_debug_run=True)

    assert result.validation_status == "passed", result.validation_errors
    assert result.evaluator_code == good_evaluator


@pytest.mark.asyncio
async def test_algorithm_trial_skipped_when_sample_unusable():
    """An unusable sample makes the trial skip instead of crashing.

    Regression: an empty/NONE sample yields ``dataset_files == {}``. The trial
    stage's ``next(iter(...))`` would then raise ``StopIteration`` (whose
    ``str()`` is empty), producing a meaningless ``[runtime:setup]`` error and
    triggering a pointless algorithm rewrite. The trial must be skipped and the
    algorithm returned untouched, with no provider call.
    """
    provider = _QueueProvider([])  # any repair call would raise
    validator = TaskValidator(provider)  # type: ignore[arg-type]

    algo_out, sample_out = await validator.checkpoint_algorithm_trial(
        algorithm_code=_GOOD_ALGORITHM,
        sample_data="NONE",
        algorithm_dir_name="sum_algorithm",
        algorithm_file_name="sum.py",
        function_to_evolve="solve",
        project_name="sum_task",
        max_repairs=2,
    )

    assert provider.calls == 0
    assert algo_out == _GOOD_ALGORITHM
    assert sample_out == "NONE"


@pytest.mark.asyncio
async def test_algorithm_trial_discards_marker_dropping_repair(monkeypatch):
    """A repair that drops the EVOLVE markers is discarded, not accepted.

    Regression: a trial failure routed to the algorithm repair could return
    code without EVOLVE markers; accepting it silently surfaced only much later
    as ``[algorithm:markers]`` in the final validate pass. The checkpoint must
    reject any repair that breaks syntax or drops the markers and keep the
    previous algorithm.
    """
    # Sample is valid JSON, but the algorithm expects data["numbers"]; running
    # it on ``{}`` raises a KeyError so the trial fails and routes to repair.
    bad_sample = "{}"

    # The "repair" strips the EVOLVE markers — a regression that must be rejected.
    marker_dropping = (
        "import json\n"
        "import sys\n\n"
        "def solve(numbers):\n"
        "    return sum(numbers)\n\n"
        "def process(data):\n"
        '    return {"result": solve(data.get("numbers", []))}\n\n'
        "def main():\n"
        "    print(json.dumps(process(json.loads(sys.argv[1]))))\n"
    )
    assert "EVOLVE_START" not in marker_dropping

    provider = _QueueProvider([marker_dropping, marker_dropping, marker_dropping])
    validator = TaskValidator(provider)  # type: ignore[arg-type]

    algo_out, _ = await validator.checkpoint_algorithm_trial(
        algorithm_code=_GOOD_ALGORITHM,
        sample_data=bad_sample,
        algorithm_dir_name="sum_algorithm",
        algorithm_file_name="sum.py",
        function_to_evolve="solve",
        project_name="sum_task",
        max_repairs=2,
    )

    # Every regressive repair is discarded; the original markers survive.
    assert "EVOLVE_START" in algo_out and "EVOLVE_END" in algo_out
