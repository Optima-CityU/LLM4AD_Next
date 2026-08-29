from __future__ import annotations

import json
from pathlib import Path

import yaml

from llm4ad.integrations.cspaper.bridge import prepare_task_from_spec
from llm4ad.integrations.cspaper.compiler import SuggestionCompiler

from .test_cspaper_compiler import REVIEW


def _task(tmp_path: Path, metrics: list[str]) -> Path:
    task = tmp_path / "task"
    algorithm = task / "algorithm"
    data = task / "data"
    algorithm.mkdir(parents=True)
    data.mkdir()
    (algorithm / "solve.py").write_text(
        "# EVOLVE_START\ndef solve(data):\n    return data\n# EVOLVE_END\n",
        encoding="utf-8",
    )
    (data / "case.json").write_text("{}", encoding="utf-8")
    config = {
        "background": "Original task background.",
        "version_control": {"local_path": "algorithm"},
        "evaluator": {
            "type": "custom",
            "module": "task_evaluator.py:TaskEvaluator",
            "metrics": metrics,
            "dataset": {"mode": "directory", "path": "data"},
        },
    }
    (task / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    (task / "task_evaluator.py").write_text(
        """# solution_cost runtime_ms visit_once
from llm4ad.evaluator.base import Metric, MetricType
METRICS = [
    Metric(name="solution_cost", type=MetricType.MINIMIZE),
    Metric(name="runtime_ms", type=MetricType.MINIMIZE),
]
""",
        encoding="utf-8",
    )
    return task


def test_prepare_injects_context_and_audits_task(tmp_path: Path) -> None:
    """A matching evaluator receives a derived CSPaper config."""
    spec = SuggestionCompiler().compile_text(REVIEW)
    spec.confirm("team")
    task = _task(tmp_path, ["solution_cost", "runtime_ms"])

    report = prepare_task_from_spec(spec, task)

    assert report.ready
    assert report.evolve_files == ["solve.py"]
    derived = yaml.safe_load((task / "config.cspaper.yaml").read_text(encoding="utf-8"))
    assert "CSPaper-derived algorithm design specification" in derived["background"]
    assert derived["cspaper_spec"]["objectives"] == ["solution_cost", "runtime_ms"]
    assert derived["evolution"]["type"] == "meoh"
    assert derived["evolution"]["objective_metrics"] == ["solution_cost", "runtime_ms"]
    audit = json.loads(
        (task / ".llm4ad/cspaper/task-audit.json").read_text(encoding="utf-8")
    )
    assert audit["ready"] is True


def test_prepare_rejects_missing_evaluator_metric(tmp_path: Path) -> None:
    """Evolution is blocked when a requested objective cannot be measured."""
    spec = SuggestionCompiler().compile_text(REVIEW)
    spec.confirm("team")
    task = _task(tmp_path, ["solution_cost"])

    report = prepare_task_from_spec(spec, task)

    assert not report.ready
    assert any("runtime_ms" in error for error in report.errors)


def test_prepare_requires_confirmation_by_default(tmp_path: Path) -> None:
    """Unconfirmed specifications cannot start the default automated flow."""
    spec = SuggestionCompiler().compile_text(REVIEW)
    task = _task(tmp_path, ["solution_cost", "runtime_ms"])

    report = prepare_task_from_spec(spec, task)

    assert not report.ready
    assert any("human confirmation is required" in error for error in report.errors)


def test_prepare_rejects_objective_direction_mismatch(tmp_path: Path) -> None:
    """A matching metric name cannot hide an inverted fitness direction."""
    spec = SuggestionCompiler().compile_text(REVIEW)
    spec.confirm("team")
    task = _task(tmp_path, ["solution_cost", "runtime_ms"])
    evaluator = task / "task_evaluator.py"
    evaluator.write_text(
        evaluator.read_text(encoding="utf-8").replace(
            'Metric(name="solution_cost", type=MetricType.MINIMIZE)',
            'Metric(name="solution_cost", type=MetricType.MAXIMIZE)',
        ),
        encoding="utf-8",
    )

    report = prepare_task_from_spec(spec, task)

    assert not report.ready
    assert any("direction mismatch" in error for error in report.errors)
