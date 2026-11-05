"""Check completed-generation reporting through the real Island GA run loop."""

import asyncio
from collections import Counter
from unittest.mock import AsyncMock, MagicMock

from llm4ad.config.evolution import IslandGAConfig
from llm4ad.infra.state import EvolutionState
from llm4ad.orchestrator.island_ga import IslandGAOrchestrator
from llm4ad.planner.base import Algorithm, EvaluationResult, InsightType


def _orchestrator(*, patience=21):
    """Build an orchestrator with deterministic local candidates.

    Args:
        patience: Number of step histories needed for plateau stopping.

    Returns:
        The orchestrator, generated candidate counts, and checkpoint cursors.
    """
    planner = MagicMock()
    planner.select_parents.side_effect = lambda population, count, **_: population[:count]
    planner.select_survivors.side_effect = lambda population, count, _: sorted(
        population, key=lambda algorithm: algorithm.score, reverse=True
    )[:count]
    orchestrator = IslandGAOrchestrator(
        planner=planner,
        coder=MagicMock(),
        dispatcher=MagicMock(),
        monitor=MagicMock(),
        config=IslandGAConfig(
            max_generations=20,
            early_stop_patience=patience,
            num_islands=3,
            island_population_size=5,
            elite_ratio=0.2,
            survival_selection_strategy="truncation",
            migration_interval=0,
            checkpoint_interval=5,
        ),
        version_control=MagicMock(),
        state_tracker=MagicMock(),
    )
    generated_counts = Counter()

    async def generate_candidate(parents, island_id, generation, sample_index=0, sample_count=1):
        del parents, sample_count
        generated_counts[generation] += 1
        candidate = Algorithm(
            id=f"{island_id}-{generation}-{sample_index}",
            insight_type=InsightType.INITIAL if generation == 0 else InsightType.MUTATION,
            description="Deterministic local candidate.",
            generation=generation,
            island_id=island_id,
            evaluation=EvaluationResult(score=1.0),
        )
        orchestrator._record_evaluation_outcome(candidate)
        return candidate

    checkpoint_cursors = []

    async def save_checkpoint():
        checkpoint_cursors.append(orchestrator.current_generation)
        return "mock-checkpoint.json"

    orchestrator._limited_generate = AsyncMock(side_effect=generate_candidate)
    orchestrator._extract_memory_cards = AsyncMock()
    orchestrator.save_checkpoint = AsyncMock(side_effect=save_checkpoint)
    orchestrator._log_timing_summary = MagicMock()
    orchestrator._log_best_metrics_comparison = MagicMock()
    return orchestrator, generated_counts, checkpoint_cursors


def test_maximum_twenty_steps_reports_twenty_without_changing_evaluations_or_cursor():
    """The result names the last completed step while the internal cursor advances."""
    orchestrator, generated_counts, checkpoint_cursors = _orchestrator()

    result = asyncio.run(orchestrator.run())

    assert result.state == EvolutionState.COMPLETED
    assert result.final_generation == 20
    assert [entry["generation"] for entry in result.history] == list(range(1, 21))
    assert generated_counts == Counter({0: 15, **dict.fromkeys(range(1, 21), 12)})
    assert result.total_evaluations == 255
    assert orchestrator.current_generation == 21
    assert checkpoint_cursors == [5, 10, 15, 20]


def test_plateau_stop_reports_last_completed_generation():
    """Plateau stopping after three steps reports three rather than its next cursor."""
    orchestrator, generated_counts, _ = _orchestrator(patience=3)

    result = asyncio.run(orchestrator.run())

    assert result.final_generation == 3
    assert [entry["generation"] for entry in result.history] == [1, 2, 3]
    assert generated_counts == Counter({0: 15, 1: 12, 2: 12, 3: 12})
    assert result.total_evaluations == 51
    assert orchestrator.current_generation == 4


def test_initialization_only_reports_generation_zero():
    """Pausing after initialization does not imply that generation one completed."""
    orchestrator, generated_counts, checkpoint_cursors = _orchestrator()

    async def pause_after_initialization(*_):
        await orchestrator.pause()

    orchestrator._extract_memory_cards.side_effect = pause_after_initialization

    result = asyncio.run(orchestrator.run())

    assert result.final_generation == 0
    assert result.history == []
    assert generated_counts == Counter({0: 15})
    assert result.total_evaluations == 15
    assert orchestrator.current_generation == 1
    assert checkpoint_cursors == []


def test_step_stop_reports_the_completed_step_before_cursor_increment():
    """A step returning false must retain its generation as the last completed one."""
    orchestrator, generated_counts, checkpoint_cursors = _orchestrator()
    original_step = orchestrator.step

    async def stop_after_step():
        _, best = await original_step()
        return False, best

    orchestrator.step = AsyncMock(side_effect=stop_after_step)

    result = asyncio.run(orchestrator.run())

    assert result.final_generation == 1
    assert [entry["generation"] for entry in result.history] == [1]
    assert generated_counts == Counter({0: 15, 1: 12})
    assert result.total_evaluations == 27
    assert orchestrator.current_generation == 1
    assert checkpoint_cursors == []
