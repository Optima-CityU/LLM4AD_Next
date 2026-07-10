"""Tests for the MindMemOS memory backend."""

from types import SimpleNamespace

import pytest

from llm4ad.config.memory import MemoryConfig
from llm4ad.planner.memory import MemoryCard, MemoryType, create_memory
from llm4ad.planner.mindmemos_memory import MindMemOSMemory


class FakeMindMemOSMemoryResource:
    """Fake MindMemOS memory API resource for unit tests."""

    def __init__(self):
        """Initialize fake call records and injectable results."""
        self.add_calls = []
        self.search_calls = []
        self.update_calls = []
        self.delete_calls = []
        self.list_calls = []
        self.search_result = SimpleNamespace(memories=[])
        self.list_result = SimpleNamespace(memories=[])
        self.add_error: Exception | None = None
        self.search_error: Exception | None = None

    def add(self, **kwargs):
        """Record a fake add call."""
        if self.add_error is not None:
            raise self.add_error
        self.add_calls.append(kwargs)
        return SimpleNamespace(code="ok", request_id="add-request", memories=[])

    def list(self, **kwargs):
        """Record a fake list call."""
        self.list_calls.append(kwargs)
        return self.list_result

    def update(self, **kwargs):
        """Record a fake update call."""
        self.update_calls.append(kwargs)
        return SimpleNamespace(code="ok")

    def delete(self, **kwargs):
        """Record a fake delete call."""
        self.delete_calls.append(kwargs)
        return SimpleNamespace(code="ok")

    def search(self, query, **kwargs):
        """Record a fake search call."""
        if self.search_error is not None:
            raise self.search_error
        self.search_calls.append({"query": query, **kwargs})
        return self.search_result


class FakeMindMemOSClient:
    """Fake MindMemOS SDK client for unit tests."""

    def __init__(self, **kwargs):
        """Initialize fake client configuration and memory resource."""
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
    """Reject incomplete MindMemOS connection settings."""
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


def test_mindmemos_backend_falls_back_to_http_when_optional_sdk_is_missing(monkeypatch):
    """Selecting MindMemOS without the optional SDK should still use HTTP APIs."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "mindmemos_sdk":
            raise ImportError("No module named mindmemos_sdk")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    memory = create_memory(
        MemoryConfig(
            type="mindmemos_cloud",
            mindmemos_base_url="http://mindmemos-api:8000",
            mindmemos_api_key="sk-test",
            mindmemos_user_id="user-1",
        )
    )

    assert memory.get_stats()["type"] == "mindmemos_cloud"
    assert memory.client.__class__.__name__ == "_HttpMindMemOSClient"


@pytest.mark.asyncio
async def test_add_card_maps_memory_card_to_mindmemos_add():
    """Map an LLM4AD memory card to the MindMemOS add API."""
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
    assert call["metadata"]["llm4ad_scope"] == "task"
    assert call["metadata"]["memory_type"] == "good_algorithm"
    assert call["metadata"]["project_id"] == "project-1"
    assert call["metadata"]["custom"] == "value"


@pytest.mark.asyncio
async def test_mindmemos_management_uses_local_view_and_enabled_filter():
    """Manage cards through MindMemOS APIs."""
    memory = MindMemOSMemory(
        _config(include_user_memory=False, include_project_memory=False),
        client_factory=FakeMindMemOSClient,
    )
    card = MemoryCard(
        id="remote-card",
        type=MemoryType.DOMAIN_KNOWLEDGE,
        title="Symmetric distances",
        content="TSP distance matrices are symmetric.",
        source="static",
    )

    await memory.upsert_card(card)
    assert memory.client.memory.update_calls[0]["memory_id"] == "remote-card"

    memory.client.memory.list_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="remote-card",
                memory="TSP distance matrices are symmetric.",
                memory_type="domain_knowledge",
                metadata={"title": "Symmetric distances"},
                status="active",
            )
        ]
    )
    assert [item.id for item in memory.list_cards()] == ["remote-card"]

    await memory.set_card_enabled("remote-card", False)
    assert memory.client.memory.update_calls[-1]["status"] == "archived"

    await memory.delete_card("remote-card")
    assert memory.client.memory.delete_calls == []


def test_get_prompt_context_formats_search_results_by_memory_type():
    """Group remote MindMemOS search hits by LLM4AD memory type."""
    memory = MindMemOSMemory(
        _config(include_user_memory=False, include_task_memory=False),
        client_factory=FakeMindMemOSClient,
    )
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
    assert memory.client.memory.search_calls[0]["filters"] == {
        "llm4ad_scope": "project",
        "project_id": "project-1",
    }
    assert "# Successful Patterns" in context
    assert "Prefer nearest-neighbor seeds." in context
    assert "# Error Reflections" in context
    assert "Avoid mutating invalid tours." in context
    assert "# Domain Knowledge" in context
    assert "Distances are symmetric." in context


def test_get_prompt_context_respects_project_memory_config():
    """Skip remote project memory search or cap its requested limit from config."""
    disabled = MindMemOSMemory(
        _config(
            include_user_memory=False,
            include_project_memory=False,
            include_task_memory=False,
        ),
        client_factory=FakeMindMemOSClient,
    )

    assert disabled.get_prompt_context("tour construction") == ""
    assert disabled.client.memory.search_calls == []

    limited = MindMemOSMemory(
        _config(include_user_memory=False, include_task_memory=False, project_memory_limit=1),
        client_factory=FakeMindMemOSClient,
    )
    limited.get_prompt_context("tour construction")

    assert limited.client.memory.search_calls[0]["top_k"] == 1


@pytest.mark.asyncio
async def test_get_prompt_context_respects_task_memory_config():
    """Skip task-scope remote search when task memory injection is disabled."""
    memory = MindMemOSMemory(
        _config(include_task_memory=False, include_project_memory=False, include_user_memory=False),
        client_factory=FakeMindMemOSClient,
    )
    await memory.upsert_card(
        MemoryCard(
            id="local-card",
            type=MemoryType.GENERAL_INSIGHT,
            title="Local",
            content="Local task lesson.",
        )
    )

    assert memory.get_prompt_context("tour construction") == ""
    assert memory.client.memory.search_calls == []


def test_get_prompt_context_returns_empty_string_when_fail_open_search_fails():
    """Suppress search failures when fail-open is enabled."""
    memory = MindMemOSMemory(_config(), client_factory=FakeMindMemOSClient)
    memory.client.memory.search_error = RuntimeError("service unavailable")

    assert memory.get_prompt_context("query") == ""
    assert memory.get_stats()["last_error"] == "service unavailable"


@pytest.mark.asyncio
async def test_add_card_raises_when_fail_open_disabled():
    """Propagate write failures when fail-open is disabled."""
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
