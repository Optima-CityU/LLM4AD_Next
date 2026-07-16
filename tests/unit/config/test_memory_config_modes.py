"""Tests for MemoryConfig retrieval and injection mode fields."""

import pytest
from pydantic import ValidationError

from llm4ad.config.memory import MemoryConfig


def test_defaults():
    """New mode fields default to auto retrieval and topk injection."""
    config = MemoryConfig()

    assert config.retrieval_mode == "auto"
    assert config.task_injection_mode == "topk"
    assert config.pinned_card_ids == []


def test_manual_mode_allows_empty_pinned_cards():
    """Manual retrieval may pin nothing (inject no shared memory)."""
    config = MemoryConfig(type="mindmemos_cloud", retrieval_mode="manual", pinned_card_ids=[])

    assert config.retrieval_mode == "manual"
    assert config.pinned_card_ids == []


def test_manual_mode_accepts_pinned_cards():
    """Manual retrieval validates when at least one card is pinned."""
    config = MemoryConfig(
        type="mindmemos_cloud",
        retrieval_mode="manual",
        pinned_card_ids=["card-1", "card-2"],
    )

    assert config.retrieval_mode == "manual"
    assert config.pinned_card_ids == ["card-1", "card-2"]


def test_manual_mode_ignored_for_local_backend():
    """Retrieval mode validation only applies to the long-term backend."""
    config = MemoryConfig(type="local_yaml", retrieval_mode="manual", pinned_card_ids=[])

    assert config.retrieval_mode == "manual"


@pytest.mark.parametrize("mode", ["topk", "weight", "random"])
def test_valid_injection_modes(mode):
    """All three injection modes are accepted."""
    config = MemoryConfig(task_injection_mode=mode)

    assert config.task_injection_mode == mode


def test_invalid_injection_mode_rejected():
    """An unknown injection mode fails validation."""
    with pytest.raises(ValidationError):
        MemoryConfig(task_injection_mode="bogus")


def test_invalid_retrieval_mode_rejected():
    """An unknown retrieval mode fails validation."""
    with pytest.raises(ValidationError):
        MemoryConfig(retrieval_mode="bogus")
