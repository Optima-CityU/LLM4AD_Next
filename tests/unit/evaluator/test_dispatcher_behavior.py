"""Tests for dispatcher behavior data aggregation."""

import asyncio
from types import SimpleNamespace

from llm4ad.config.evaluator import CustomEvaluatorConfig
from llm4ad.evaluator.base import EvaluationResult
from llm4ad.evaluator.behavior import BehaviorData, BehaviorVisualization
from llm4ad.evaluator.dispatcher import EvaluationDispatcher


def _make_behavior(instance_id: str, observation: str = "obs") -> BehaviorData:
    """Create a BehaviorData with a dummy visualization."""
    return BehaviorData(
        observation=observation,
        visualizations=[
            BehaviorVisualization(
                label="Test Viz",
                media_type="image/png",
                data_base64="iVBORw0KGgo=",  # minimal valid base64
            )
        ],
        instance_id=instance_id,
    )


def _make_result(
    score: float,
    behavior: BehaviorData | None = None,
    success: bool = True,
) -> EvaluationResult:
    """Create an EvaluationResult with optional behavior."""
    return EvaluationResult(
        score=score,
        metrics={"reward": score * 100},
        success=success,
        behavior=behavior,
        duration_ms=10.0,
    )


class _TimeoutRecordingEvaluator:
    """Minimal evaluator used to observe the context built by the dispatcher."""

    def __init__(self) -> None:
        self.contexts = []

    async def evaluate(self, cfg):
        self.contexts.append(cfg)
        return _make_result(1.0)


class TestDispatcherBehaviorAggregation:
    """Tests for _aggregate_results behavior data handling."""

    def _dispatcher(self) -> EvaluationDispatcher:
        """Create a minimal dispatcher (only _aggregate_results is tested)."""
        from unittest.mock import patch

        with patch.object(EvaluationDispatcher, "__init__", lambda self, *a, **kw: None):
            d = EvaluationDispatcher.__new__(EvaluationDispatcher)
        return d

    def test_single_result_passes_behavior_through(self):
        """Single result's behavior should be returned unchanged."""
        bd = _make_behavior("inst_1")
        result = _make_result(0.8, behavior=bd)

        aggregated = self._dispatcher()._aggregate_results([result])

        assert aggregated.behavior is bd

    def test_multiple_results_picks_best_behavior(self):
        """Best-scoring instance's behavior should be primary."""
        bd1 = _make_behavior("inst_1", "low score")
        bd2 = _make_behavior("inst_2", "high score")
        bd3 = _make_behavior("inst_3", "mid score")

        results = [
            _make_result(0.5, behavior=bd1),
            _make_result(0.9, behavior=bd2),
            _make_result(0.3, behavior=bd3),
        ]

        aggregated = self._dispatcher()._aggregate_results(results)

        assert aggregated.behavior is bd2
        assert aggregated.behavior.observation == "high score"

        all_behavior = aggregated.metadata.get("all_behavior", [])
        assert len(all_behavior) == 3

    def test_no_behavior_returns_none(self):
        """Results without behavior should aggregate to None behavior."""
        results = [
            _make_result(0.5),
            _make_result(0.9),
        ]

        aggregated = self._dispatcher()._aggregate_results(results)

        assert aggregated.behavior is None

    def test_partial_behavior_picks_best_with_behavior(self):
        """When only some results have behavior, pick best among those."""
        bd = _make_behavior("inst_2", "only one with behavior")
        results = [
            _make_result(0.9),  # best score but no behavior
            _make_result(0.5, behavior=bd),
        ]

        aggregated = self._dispatcher()._aggregate_results(results)

        assert aggregated.behavior is bd
        all_behavior = aggregated.metadata.get("all_behavior", [])
        assert len(all_behavior) == 1


def test_dispatch_batch_uses_configured_evaluator_timeout():
    """The evaluator timeout must not fall back to EvalContext's 60-second default."""
    dispatcher = EvaluationDispatcher.__new__(EvaluationDispatcher)
    dispatcher.config = CustomEvaluatorConfig(module="example.py:ExampleEvaluator", timeout=600)
    dispatcher._parallel = True
    dispatcher._data_files = ["/tmp/input"]
    dispatcher._behavior_storage = "none"
    dispatcher._semaphore = asyncio.Semaphore(1)
    evaluator = _TimeoutRecordingEvaluator()
    dispatcher._create_evaluator = lambda: evaluator

    algorithm = SimpleNamespace(worktree=SimpleNamespace(path="/tmp/worktree"))
    results = asyncio.run(dispatcher.dispatch_batch([algorithm]))

    assert results[0].success is True
    assert [context.timeout for context in evaluator.contexts] == [600]


def test_custom_evaluator_can_import_a_module_from_the_project_root(
    tmp_path,
    monkeypatch,
) -> None:
    """A nested evaluator may share helpers located at the task project root."""
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (tmp_path / "shared_import_probe.py").write_text("VALUE = 42\n", encoding="utf-8")
    evaluator_path = case_dir / "shared_import_probe_evaluator.py"
    evaluator_path.write_text(
        "from shared_import_probe import VALUE\n"
        "class SharedImportProbeEvaluator:\n"
        "    marker = VALUE\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    evaluator_class = EvaluationDispatcher._import_custom_evaluator(
        "case/shared_import_probe_evaluator.py:SharedImportProbeEvaluator"
    )

    assert evaluator_class.marker == 42
