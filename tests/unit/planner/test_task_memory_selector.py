"""Tests for task-memory injection selectors."""

import pytest

from llm4ad.planner.task_memory_selector import (
    BaseTaskMemorySelector,
    RandomSelector,
    TaskMemoryCandidate,
    TopKSelector,
    WeightSelector,
    create_task_memory_selector,
)


def _candidates() -> list[TaskMemoryCandidate]:
    """Build a fixed candidate pool with distinct scores and weights."""
    return [
        TaskMemoryCandidate(key="a", score=0.1, metadata={"injection_weight": 5.0}),
        TaskMemoryCandidate(key="b", score=0.9, metadata={"injection_weight": 1.0}),
        TaskMemoryCandidate(key="c", score=0.5, metadata={}),
        TaskMemoryCandidate(key="d", score=0.7, metadata={"injection_weight": 3.0}),
    ]


def test_registered_modes():
    """All three injection modes should be registered."""
    assert set(BaseTaskMemorySelector.list()) == {"topk", "weight", "random"}


def test_topk_orders_by_score_desc():
    """TopK keeps the highest retrieval-scored candidates in order."""
    selected = TopKSelector().select(_candidates(), 2)

    assert [c.key for c in selected] == ["b", "d"]


def test_topk_treats_missing_score_as_lowest():
    """Candidates without a score rank below scored ones."""
    candidates = [
        TaskMemoryCandidate(key="scored", score=0.2),
        TaskMemoryCandidate(key="unscored", score=None),
    ]

    selected = TopKSelector().select(candidates, 1)

    assert [c.key for c in selected] == ["scored"]


def test_weight_orders_by_injection_weight_desc():
    """Weight selector orders by the injection_weight metadata."""
    selected = WeightSelector().select(_candidates(), 2)

    assert [c.key for c in selected] == ["a", "d"]


def test_weight_defaults_missing_weight_to_one():
    """A candidate without an explicit weight defaults to 1.0."""
    candidates = [
        TaskMemoryCandidate(key="light", score=0.9, metadata={"injection_weight": 0.5}),
        TaskMemoryCandidate(key="default", score=0.1, metadata={}),
    ]

    selected = WeightSelector().select(candidates, 1)

    assert [c.key for c in selected] == ["default"]


def test_weight_ignores_invalid_weight():
    """Non-numeric weights fall back to the default weight."""
    candidate = TaskMemoryCandidate(key="x", metadata={"injection_weight": "not-a-number"})

    assert candidate.weight() == 1.0


def test_random_is_reproducible_with_seed():
    """Seeded random selection is deterministic across runs."""
    candidates = _candidates()

    first = RandomSelector({"seed": 42}).select(candidates, 2)
    second = RandomSelector({"seed": 42}).select(candidates, 2)

    assert [c.key for c in first] == [c.key for c in second]


def test_random_returns_all_when_limit_exceeds_pool():
    """When limit >= pool size, all candidates are returned (shuffled)."""
    candidates = _candidates()

    selected = RandomSelector({"seed": 1}).select(candidates, 10)

    assert {c.key for c in selected} == {c.key for c in candidates}


@pytest.mark.parametrize("mode", ["topk", "weight", "random"])
def test_non_positive_limit_returns_empty(mode):
    """A non-positive limit yields no candidates for every mode."""
    selector = create_task_memory_selector(mode)

    assert selector.select(_candidates(), 0) == []
    assert selector.select(_candidates(), -3) == []


@pytest.mark.parametrize("mode", ["topk", "weight", "random"])
def test_empty_candidates_returns_empty(mode):
    """No candidates yields an empty result for every mode."""
    selector = create_task_memory_selector(mode)

    assert selector.select([], 5) == []


def test_factory_falls_back_to_topk_for_unknown_mode():
    """Unknown or empty modes fall back to the topk selector."""
    assert isinstance(create_task_memory_selector("bogus"), TopKSelector)
    assert isinstance(create_task_memory_selector(None), TopKSelector)


def test_factory_creates_requested_mode():
    """The factory returns the selector matching the requested mode."""
    assert isinstance(create_task_memory_selector("weight"), WeightSelector)
    assert isinstance(create_task_memory_selector("random"), RandomSelector)
