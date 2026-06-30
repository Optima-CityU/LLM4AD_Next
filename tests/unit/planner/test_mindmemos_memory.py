"""Tests for the MindMemOS memory backend."""

from types import SimpleNamespace

import pytest

from llm4ad.planner.memory import MemoryCard, MemoryType
from llm4ad.planner.mindmemos_memory import MindMemOSMemory


class FakeMindMemOSMemoryResource:
    def __init__(self):
        self.add_calls = []
        self.search_calls = []
        self.search_result = SimpleNamespace(memories=[])
        self.add_error: Exception | None = None
        self.search_error: Exception | None = None

    def add(self, **kwargs):
        if self.add_error is not None:
            raise self.add_error
        self.add_calls.append(kwargs)
        return SimpleNamespace(code="ok", request_id="add-request", memories=[])

    def search(self, query, **kwargs):
        if self.search_error is not None:
            raise self.search_error
        self.search_calls.append({"query": query, **kwargs})
        return self.search_result


class FakeMindMemOSClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.memory = FakeMindMemOSMemoryResource()


def _config(**overrides):
    config = {
        "mindmemos_base_url": "http://mindmemos-api:8000",
        "mindmemos_api_key": "sk-test",
        "mindmemos_user_id": "user-1",
        "mindmemos_app_id": "llm4ad",
        "mindmemos_agent_id": "planner",
        "mindmemos_session_id": "task-1",
        "mindmemos_project_id": "project-1",
        "mindmemos_fail_open": True,
        "max_prompt_cards": 3,
    }
    config.update(overrides)
    return config


def test_requires_base_url_api_key_and_user_id():
    with pytest.raises(ValueError, match="mindmemos_base_url"):
        MindMemOSMemory(
            _config(mindmemos_base_url=""),
            client_factory=FakeMindMemOSClient,
        )

    with pytest.raises(ValueError, match="mindmemos_api_key"):
        MindMemOSMemory(
            _config(mindmemos_api_key=""),
            client_factory=FakeMindMemOSClient,
        )

    with pytest.raises(ValueError, match="mindmemos_user_id"):
        MindMemOSMemory(
            _config(mindmemos_user_id=""),
            client_factory=FakeMindMemOSClient,
        )


@pytest.mark.asyncio
async def test_add_card_maps_memory_card_to_mindmemos_add():
    memory = MindMemOSMemory(_config(), client_factory=FakeMindMemOSClient)
    card = MemoryCard(
        id="card-1",
        type=MemoryType.GOOD_ALGORITHM,
        title="Use constructive initialization",
        content="Seed the population with nearest-neighbor tours.",
        source="auto",
        score=0.91,
        generation=4,
        algorithm_id="algo-7",
        tags=["tsp", "initialization"],
        metadata={"custom": "value"},
    )

    await memory.add_card(card)

    call = memory.client.memory.add_calls[0]
    assert call["user_id"] == "user-1"
    assert call["app_id"] == "llm4ad"
    assert call["agent_id"] == "planner"
    assert call["session_id"] == "task-1"
    assert call["mode"] == "sync"
    assert call["score"] == 0.91
    assert call["task_id"] == "task-1"
    assert call["messages"][0].role == "assistant"
    assert "Use constructive initialization" in call["messages"][0].content
    assert "Seed the population" in call["messages"][0].content
    assert call["metadata"]["source"] == "llm4ad"
    assert call["metadata"]["memory_type"] == "good_algorithm"
    assert call["metadata"]["project_id"] == "project-1"
    assert call["metadata"]["custom"] == "value"


def test_get_prompt_context_formats_search_results_by_memory_type():
    memory = MindMemOSMemory(_config(), client_factory=FakeMindMemOSClient)
    memory.client.memory.search_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="remote-1",
                memory="Prefer nearest-neighbor seeds.",
                memory_type="good_algorithm",
            ),
            SimpleNamespace(
                id="remote-2",
                memory="Avoid mutating invalid tours.",
                memory_type="error_reflection",
            ),
            SimpleNamespace(
                id="remote-3",
                memory="Distances are symmetric.",
                metadata={"memory_type": "domain_knowledge"},
            ),
        ]
    )

    context = memory.get_prompt_context("tour construction")

    assert memory.client.memory.search_calls[0]["query"] == "tour construction"
    assert memory.client.memory.search_calls[0]["top_k"] == 3
    assert memory.client.memory.search_calls[0]["filters"] == {"project_id": "project-1"}
    assert "# Successful Patterns" in context
    assert "Prefer nearest-neighbor seeds." in context
    assert "# Error Reflections" in context
    assert "Avoid mutating invalid tours." in context
    assert "# Domain Knowledge" in context
    assert "Distances are symmetric." in context


def test_get_prompt_context_returns_empty_string_when_fail_open_search_fails():
    memory = MindMemOSMemory(_config(), client_factory=FakeMindMemOSClient)
    memory.client.memory.search_error = RuntimeError("service unavailable")

    assert memory.get_prompt_context("query") == ""
    assert memory.get_stats()["last_error"] == "service unavailable"


@pytest.mark.asyncio
async def test_add_card_raises_when_fail_open_disabled():
    memory = MindMemOSMemory(
        _config(mindmemos_fail_open=False),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.add_error = RuntimeError("write failed")

    with pytest.raises(RuntimeError, match="write failed"):
        await memory.add_card(
            MemoryCard(
                type=MemoryType.GENERAL_INSIGHT,
                title="General",
                content="Useful memory",
            )
        )
