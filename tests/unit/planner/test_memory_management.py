"""Tests for common memory card management behavior."""

from pathlib import Path

import pytest

from llm4ad.planner.memory import Memory, MemoryCard, MemoryType


def _card(
    card_id: str = "card-1",
    title: str = "Nearest neighbor seed",
    content: str = "Use nearest-neighbor construction before local search.",
    memory_type: MemoryType = MemoryType.GOOD_ALGORITHM,
    enabled: bool = True,
) -> MemoryCard:
    return MemoryCard(
        id=card_id,
        type=memory_type,
        title=title,
        content=content,
        enabled=enabled,
        source="static",
    )


@pytest.mark.asyncio
async def test_local_yaml_lists_updates_disables_and_deletes_cards(tmp_path: Path):
    """Manage local YAML cards and exclude disabled cards from prompts."""
    memory = Memory({"max_prompt_cards": 5, "persist": True})
    memory.set_memory_dir(tmp_path)

    await memory.upsert_card(_card())

    cards = memory.list_cards()
    assert len(cards) == 1
    assert cards[0].title == "Nearest neighbor seed"
    assert "Nearest-neighbor" not in memory.get_prompt_context()
    assert "nearest-neighbor" in memory.get_prompt_context()

    await memory.set_card_enabled("card-1", False)
    assert memory.list_cards()[0].enabled is False
    assert memory.get_prompt_context() == ""

    await memory.upsert_card(
        _card(title="Two opt cleanup", content="Run 2-opt after construction.", enabled=True)
    )
    assert memory.list_cards()[0].title == "Two opt cleanup"
    assert "2-opt" in memory.get_prompt_context()

    await memory.delete_card("card-1")
    assert memory.list_cards() == []
    assert not list(tmp_path.glob("*.yaml"))


def test_memory_card_enabled_round_trips_through_yaml(tmp_path: Path):
    """Persist the enabled flag through MemoryCard YAML serialization."""
    card = _card(enabled=False)

    path = card.to_yaml_file(tmp_path)
    loaded = MemoryCard.from_yaml_file(path)

    assert loaded.enabled is False


@pytest.mark.asyncio
async def test_local_yaml_respects_task_memory_injection_config():
    """Disable or limit task-scoped memory prompt injection."""
    disabled = Memory({"include_task_memory": False})
    await disabled.upsert_card(_card())
    assert disabled.get_prompt_context() == ""

    limited = Memory({"task_memory_limit": 1})
    await limited.upsert_card(_card(card_id="card-1", title="First", content="First lesson."))
    await limited.upsert_card(_card(card_id="card-2", title="Second", content="Second lesson."))

    context = limited.get_prompt_context()
    assert "First lesson" in context
    assert "Second lesson" not in context
