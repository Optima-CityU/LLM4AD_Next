"""Regression tests for evaluator-error-guided individual repair."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from llm4ad.evaluator.base import EvaluationResult as DispatcherEvaluationResult
from llm4ad.infra.version_control.base import WorktreeInfo
from llm4ad.orchestrator.island_ga import (
    DiverseIslandGAOrchestrator,
    IslandGAOrchestrator,
    is_repairable_evaluation_error,
)
from llm4ad.planner.base import Algorithm, InsightType


def _algorithm() -> Algorithm:
    return Algorithm(
        id="candidate",
        insight_type=InsightType.MUTATION,
        name="candidate",
        description="Improve the packing construction.",
        worktree=WorktreeInfo(
            name="candidate",
            path="/tmp/candidate",
            branch="candidate",
            commit_hash="before-repair",
            created_at=0,
            last_used_at=0,
        ),
    )


def _orchestrator(
    *results: DispatcherEvaluationResult,
    max_retries: int = 1,
    orchestrator_type=IslandGAOrchestrator,
):
    orchestrator = object.__new__(orchestrator_type)
    orchestrator.dispatcher = SimpleNamespace(
        config=SimpleNamespace(max_retries=max_retries),
        dispatch_batch=AsyncMock(side_effect=[[result] for result in results]),
    )
    orchestrator.planner = SimpleNamespace(
        implement=AsyncMock(side_effect=lambda algorithm, **_: algorithm),
        build=AsyncMock(return_value=True),
    )
    orchestrator.version_control = MagicMock()
    orchestrator.version_control.commit_changes.return_value = SimpleNamespace(
        success=True,
        error=None,
    )
    orchestrator.version_control.get_changed_files.return_value = []
    orchestrator.version_control.get_diff_stats.return_value = {}
    orchestrator.background = "Pack circles in a unit square."
    orchestrator.state_tracker = MagicMock()
    orchestrator.monitor = MagicMock()
    return orchestrator


def test_algorithm_failure_is_not_recorded_as_a_zero_score() -> None:
    """A failed execution has an error but no synthetic evaluation score."""
    algorithm = _algorithm()

    algorithm.set_evaluation_failure("constraint violation")

    assert algorithm.evaluation is None
    assert algorithm.evaluation_failure == "constraint violation"
    assert algorithm.is_evaluated() is False


def test_exported_failure_shows_error_without_a_score() -> None:
    """Evaluation artifacts expose the real failure and omit a numeric score."""
    algorithm = _algorithm()
    algorithm.set_evaluation_failure("constraint violation")

    markdown = algorithm._to_markdown("evaluation")

    assert "constraint violation" in markdown
    assert "**Score:**" not in markdown


@pytest.mark.parametrize(
    "error",
    [
        "Candidate validation failed: circles overlap",
        "AssertionError: returned radius is not finite",
        "RuntimeError: generated expression violates a constraint",
        "NameError: name 'radius' is not defined",
        "ModuleNotFoundError: No module named 'candidate_helper'",
    ],
)
def test_candidate_errors_are_repairable(error: str) -> None:
    """Candidate implementation diagnostics can guide a rewrite."""
    assert is_repairable_evaluation_error(error) is True


@pytest.mark.parametrize(
    "error",
    [
        "Evaluation timed out after 600 seconds",
        "503 Service Temporarily Unavailable",
        "401 invalid API key",
        "Connection refused by model provider",
        "Permission denied while starting the evaluation container",
        "No space left on device",
    ],
)
def test_infrastructure_errors_are_not_repairable(error: str) -> None:
    """Infrastructure failures must not spend tokens rewriting valid code."""
    assert is_repairable_evaluation_error(error) is False


@pytest.mark.asyncio
async def test_failed_candidate_is_rewritten_and_evaluated_again() -> None:
    """A repairable failure rewrites in place before another evaluation."""
    first_error = "Candidate validation failed: circles 3 and 8 overlap"
    orchestrator = _orchestrator(
        DispatcherEvaluationResult(
            score=0,
            metrics={},
            success=False,
            error_message=first_error,
        ),
        DispatcherEvaluationResult(
            score=2.5,
            metrics={"validity": 1.0},
            success=True,
        ),
    )
    algorithm = _algorithm()

    repaired = await orchestrator._evaluate_algorithm(algorithm)

    assert repaired.is_evaluated() is True
    assert repaired.score == 2.5
    assert repaired.evaluation_failure is None
    assert orchestrator.dispatcher.dispatch_batch.await_count == 2
    orchestrator.planner.implement.assert_awaited_once()
    repair_prompt = orchestrator.planner.implement.await_args.kwargs["task_description"]
    assert first_error in repair_prompt
    assert "untrusted diagnostic data" in repair_prompt
    orchestrator.version_control.delete_worktree.assert_not_called()


@pytest.mark.asyncio
async def test_successful_evaluation_materializes_inheritable_candidate_state(
    tmp_path,
) -> None:
    """Evaluator discoveries must be committed so descendants inherit the new state."""
    candidate_path = tmp_path / "model_spec.py"
    candidate_path.write_text(
        "MODEL_SPEC = {'parameters': {'phase': "
        "{'initial': 0.0, 'lower': -0.5, 'upper': 0.5}}}\n",
        encoding="utf-8",
    )
    orchestrator = _orchestrator(
        DispatcherEvaluationResult(
            score=2.5,
            metrics={"validity": 1.0},
            success=True,
            evolution_feedback={
                "candidate_update": {
                    "candidate_file": "model_spec.py",
                    "candidate_symbol": "MODEL_SPEC",
                    "patch": {"parameters": {"phase": {"initial": 0.25}}},
                }
            },
        ),
    )
    algorithm = _algorithm()
    assert algorithm.worktree is not None
    algorithm.worktree.path = str(tmp_path)

    evaluated = await orchestrator._evaluate_algorithm(algorithm)

    assert evaluated.evaluation is not None
    assert "'initial': 0.25" in candidate_path.read_text(encoding="utf-8")
    orchestrator.version_control.commit_changes.assert_called_once()


@pytest.mark.asyncio
async def test_successful_code_evaluation_does_not_create_candidate_state_commit() -> None:
    """Evaluators without a candidate update keep the ordinary code path unchanged."""
    orchestrator = _orchestrator(
        DispatcherEvaluationResult(
            score=2.5,
            metrics={"validity": 1.0},
            success=True,
            evolution_feedback={"diagnostic": "ordinary code feedback"},
        ),
    )

    evaluated = await orchestrator._evaluate_algorithm(_algorithm())

    assert evaluated.score == 2.5
    orchestrator.version_control.commit_changes.assert_not_called()


@pytest.mark.asyncio
async def test_infrastructure_failure_is_preserved_without_model_rewrite() -> None:
    """A service failure is retained for reflection without a model call."""
    error = "503 Service Temporarily Unavailable"
    orchestrator = _orchestrator(
        DispatcherEvaluationResult(
            score=0,
            metrics={},
            success=False,
            error_message=error,
        ),
    )
    algorithm = _algorithm()

    failed = await orchestrator._evaluate_algorithm(algorithm)

    assert failed.is_evaluated() is False
    assert failed.evaluation is None
    assert failed.evaluation_failure == error
    orchestrator.planner.implement.assert_not_awaited()
    orchestrator.version_control.delete_worktree.assert_called_once_with(
        algorithm.worktree,
        force=True,
    )


@pytest.mark.asyncio
async def test_failure_memory_receives_the_preserved_evaluator_error() -> None:
    """Failure reflection extraction receives the actual evaluator diagnostic."""
    error = "Candidate validation failed: radius exceeds boundary"
    algorithm = _algorithm()
    algorithm.set_evaluation_failure(error)
    extractor = SimpleNamespace(
        reset_generation=MagicMock(),
        extract_from_good=AsyncMock(),
        extract_from_bad=AsyncMock(),
        extract_from_failure=AsyncMock(return_value=None),
    )
    orchestrator = _orchestrator(max_retries=0)
    orchestrator.planner = SimpleNamespace(
        memory=SimpleNamespace(extractor=extractor),
    )
    orchestrator.current_generation = 3
    orchestrator._llm_semaphore = None

    await orchestrator._extract_memory_cards([algorithm], "packing background")

    extractor.extract_from_failure.assert_awaited_once_with(
        algorithm,
        error,
        3,
        "packing background",
    )


@pytest.mark.asyncio
async def test_memory_extraction_reserves_first_slot_for_generation_best() -> None:
    """Low-score cards from earlier islands must not consume the best-card slot."""
    best = _algorithm()
    best.id = "best"
    best.set_evaluation_result(3.0, metrics={})
    low = _algorithm()
    low.id = "low"
    low.set_evaluation_result(1.0, metrics={})
    call_order: list[str] = []

    async def good(algo, *_args):
        call_order.append(f"good:{algo.id}")
        return SimpleNamespace(id=f"good-{algo.id}") if algo.id == "best" else None

    async def bad(algo, *_args):
        call_order.append(f"bad:{algo.id}")
        return SimpleNamespace(id=f"bad-{algo.id}") if algo.id == "low" else None

    extractor = SimpleNamespace(
        reset_generation=MagicMock(),
        extract_from_good=AsyncMock(side_effect=good),
        extract_from_bad=AsyncMock(side_effect=bad),
        extract_from_failure=AsyncMock(return_value=None),
    )
    add_cards = AsyncMock()
    orchestrator = _orchestrator(max_retries=0)
    orchestrator.planner = SimpleNamespace(
        memory=SimpleNamespace(extractor=extractor, add_cards=add_cards),
    )
    orchestrator.current_generation = 2
    orchestrator._llm_semaphore = None

    await orchestrator._extract_memory_cards([low, best], "packing")

    assert call_order[0] == "good:best"
    add_cards.assert_awaited_once()
    assert [card.id for card in add_cards.await_args.args[0]][0] == "good-best"


@pytest.mark.asyncio
async def test_elite_reevaluation_uses_high_fidelity_profile() -> None:
    """Only configured top offspring receive the optional second evaluation stage."""
    high = _algorithm()
    high.id = "high"
    high.set_evaluation_result(2.0, metrics={})
    low = _algorithm()
    low.id = "low"
    low.set_evaluation_result(1.0, metrics={})
    orchestrator = _orchestrator(
        max_retries=0,
        orchestrator_type=DiverseIslandGAOrchestrator,
    )
    orchestrator.config = SimpleNamespace(elite_reevaluation_count=1)
    orchestrator._total_evaluation_attempts = 0
    orchestrator._total_successful_evaluations = 0
    orchestrator.dispatcher.dispatch_batch = AsyncMock(
        return_value=[
            DispatcherEvaluationResult(
                score=2.5,
                metrics={"validity": 1.0},
                evolution_feedback={"optimized_parameters": {"phase": 0.4}},
            )
        ]
    )

    await orchestrator._reevaluate_elite_offspring([low, high])

    orchestrator.dispatcher.dispatch_batch.assert_awaited_once_with(
        algorithms=[high],
        evaluation_profile="elite",
    )
    assert high.score == 2.5
    assert high.evaluation is not None
    assert high.evaluation.evolution_feedback["optimized_parameters"] == {"phase": 0.4}
    assert low.score == 1.0
