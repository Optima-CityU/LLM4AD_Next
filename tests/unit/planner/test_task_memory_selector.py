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
    """Build a fixed candidate pool with distinct retrieval scores."""
    return [
        TaskMemoryCandidate(key="a", score=0.1),
        TaskMemoryCandidate(key="b", score=0.9),
        TaskMemoryCandidate(key="c", score=0.5),
        TaskMemoryCandidate(key="d", score=0.7),
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


def test_weight_is_reproducible_with_seed():
    """Seeded roulette-wheel sampling is deterministic across runs."""
    candidates = _candidates()

    first = WeightSelector({"seed": 7}).select(candidates, 2)
    second = WeightSelector({"seed": 7}).select(candidates, 2)

    assert [c.key for c in first] == [c.key for c in second]


def test_weight_favors_most_similar_by_rank():
    """The most-similar (rank 0) candidate is selected more often than the least."""
    # Pool is ordered most-similar first; keys encode the rank position.
    candidates = [TaskMemoryCandidate(key=f"r{i}") for i in range(10)]

    top_first = 0
    bottom_first = 0
    trials = 2000
    for seed in range(trials):
        selected = WeightSelector({"seed": seed}).select(candidates, 1)
        if selected[0].key == "r0":
            top_first += 1
        elif selected[0].key == "r9":
            bottom_first += 1

    # Linear decay (weight 1 vs 1/10) must bias the single pick toward rank 0.
    assert top_first > bottom_first


def test_weight_lambda_zero_favors_most_recent():
    """With lambda=0 the weight is pure recency, favoring the newest candidate."""
    # Least similar (last in pool) but newest timestamp.
    candidates = [
        TaskMemoryCandidate(key="old_similar", timestamp=100.0),
        TaskMemoryCandidate(key="new_distant", timestamp=999.0),
    ]

    newest_first = 0
    trials = 2000
    for seed in range(trials):
        selected = WeightSelector({"seed": seed, "lambda": 0.0}).select(candidates, 1)
        if selected[0].key == "new_distant":
            newest_first += 1

    # Pure recency: the newest card (recency rank 0) must dominate.
    assert newest_first > trials * 0.6


def test_weight_lambda_one_ignores_recency():
    """With lambda=1 the weight is pure similarity, ignoring timestamps."""
    # Most similar (rank 0) but oldest; least similar but newest.
    candidates = [
        TaskMemoryCandidate(key="similar_old", timestamp=1.0),
        TaskMemoryCandidate(key="distant_new", timestamp=999.0),
    ]

    similar_first = 0
    trials = 2000
    for seed in range(trials):
        selected = WeightSelector({"seed": seed, "lambda": 1.0}).select(candidates, 1)
        if selected[0].key == "similar_old":
            similar_first += 1

    # Pure similarity: the most-similar card must dominate despite being oldest.
    assert similar_first > trials * 0.6


def test_weight_lambda_out_of_range_falls_back_to_default():
    """Invalid lambda values fall back to the 0.5 default without error."""
    candidates = _candidates()

    # Should not raise and should return a valid subset.
    selected = WeightSelector({"seed": 1, "lambda": "bogus"}).select(candidates, 2)

    assert len(selected) == 2


def test_weight_returns_all_when_limit_exceeds_pool():
    """When limit >= pool size, all candidates are returned."""
    candidates = _candidates()

    selected = WeightSelector({"seed": 1}).select(candidates, 10)

    assert {c.key for c in selected} == {c.key for c in candidates}


def test_weight_single_candidate_is_selected():
    """A single-candidate pool always returns that candidate."""
    candidates = [TaskMemoryCandidate(key="only")]

    selected = WeightSelector({"seed": 3}).select(candidates, 1)

    assert [c.key for c in selected] == ["only"]


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
