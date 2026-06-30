"""Tests for memory module factories and registration."""

from types import SimpleNamespace

import pytest

from llm4ad.config.memory import AutoExtractionConfig, MemoryConfig
from llm4ad.planner.memory import (
    BaseMemory,
    BaseMemoryExtractor,
    Memory,
    MemoryExtractor,
    create_memory,
    create_memory_extractor,
)


class DummyProvider:
    """Provider stub for extractor construction."""


def test_create_memory_uses_local_yaml_by_default():
    """Default memory config should create the current local YAML memory."""
    config = MemoryConfig()

    memory = create_memory(config)

    assert isinstance(memory, Memory)
    assert memory.max_prompt_cards == config.max_prompt_cards


def test_create_memory_reports_unknown_type():
    """Unknown memory type should raise a clear registry error."""
    config = MemoryConfig(type="missing_memory")

    with pytest.raises(KeyError, match="missing_memory"):
        create_memory(config)


def test_create_memory_imports_custom_module(tmp_path, monkeypatch):
    """Memory factory should import a user module before resolving the type."""
    module_path = tmp_path / "custom_memory_module.py"
    module_path.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "from typing import Any",
                "from llm4ad.planner.memory import BaseMemory",
                "",
                "@BaseMemory.register('custom_memory_for_test')",
                "class CustomMemory(BaseMemory):",
                "    def __init__(self, config: dict[str, Any]):",
                "        super().__init__(config)",
                "        self.memory_dir = None",
                "    def set_memory_dir(self, memory_dir: Path) -> None:",
                "        self.memory_dir = memory_dir",
                "    def load_static_cards(self, inline_cards: list[Any]) -> None:",
                "        self.inline_cards = inline_cards",
                "    async def add_card(self, card, persist=None) -> None:",
                "        self.card = card",
                "    def get_prompt_context(self, query: str = '', max_cards=None) -> str:",
                "        return 'custom context'",
                "    def get_stats(self) -> dict[str, Any]:",
                "        return {'custom': True}",
                "    def clear(self) -> None:",
                "        self.cleared = True",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    config = MemoryConfig(type="custom_memory_for_test", module="custom_memory_module")

    memory = create_memory(config)

    assert isinstance(memory, BaseMemory)
    assert memory.get_prompt_context() == "custom context"


def test_create_extractor_uses_llm_card_extractor_by_default():
    """Default auto-extraction config should create the current LLM extractor."""
    config = AutoExtractionConfig()

    extractor = create_memory_extractor(DummyProvider(), config)

    assert isinstance(extractor, MemoryExtractor)


def test_create_extractor_imports_custom_module(tmp_path, monkeypatch):
    """Extractor factory should import a user module before resolving the type."""
    module_path = tmp_path / "custom_extractor_module.py"
    module_path.write_text(
        "\n".join(
            [
                "from typing import Any",
                "from llm4ad.planner.memory import BaseMemoryExtractor",
                "",
                "@BaseMemoryExtractor.register('custom_extractor_for_test')",
                "class CustomExtractor(BaseMemoryExtractor):",
                "    def __init__(self, provider: Any, config: Any):",
                "        super().__init__(provider, config)",
                "        self.reset = False",
                "    def reset_generation(self) -> None:",
                "        self.reset = True",
                "    async def extract_from_good(self, algorithm, population, generation, background=''):",
                "        return None",
                "    async def extract_from_bad(self, algorithm, population, generation, background=''):",
                "        return None",
                "    async def extract_from_failure(self, algorithm, error, generation, background=''):",
                "        return None",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    config = AutoExtractionConfig(
        type="custom_extractor_for_test",
        module="custom_extractor_module",
    )

    extractor = create_memory_extractor(SimpleNamespace(), config)

    assert isinstance(extractor, BaseMemoryExtractor)
    extractor.reset_generation()
    assert extractor.reset is True
