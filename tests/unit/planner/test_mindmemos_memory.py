"""Tests for the MindMemOS memory backend."""

import time
from types import SimpleNamespace

import pytest
from loguru import logger
from pydantic import ValidationError

from llm4ad.config.memory import MemoryConfig
from llm4ad.planner.base import Algorithm, CodeArtifact, GenerationMetadata, InsightType
from llm4ad.planner.memory import MemoryCard, MemoryType, create_memory, create_memory_extractor
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


class SharedResourceMindMemOSClient:
    """Fake client that exposes created clients for timeout-sensitive tests."""

    instances: list["SharedResourceMindMemOSClient"] = []

    def __init__(self, **kwargs):
        """Initialize fake client configuration and shared memory resource."""
        self.kwargs = kwargs
        self.memory = FakeMindMemOSMemoryResource()
        self.__class__.instances.append(self)


class SlowScopeAwareMindMemOSMemoryResource(FakeMindMemOSMemoryResource):
    """Fake search resource that makes serial multi-scope retrieval visible."""

    def __init__(self, delay_seconds: float = 0.08):
        """Initialize with a per-search delay and scope-specific hits."""
        super().__init__()
        self.delay_seconds = delay_seconds

    def search(self, query, **kwargs):
        """Return one deterministic hit for each requested scope."""
        time.sleep(self.delay_seconds)
        self.search_calls.append({"query": query, **kwargs})
        agent_id = str(kwargs.get("agent_id") or "")
        scope_label = {
            "task": "Task scoped nearest-neighbor repair.",
            "project": "Project scoped crossover lesson.",
            "global": "Global mutation lesson.",
        }.get(agent_id, "Unknown memory.")
        return SimpleNamespace(
            memories=[
                SimpleNamespace(
                    id=f"{agent_id}-memory",
                    memory=scope_label,
                    memory_type="good_algorithm",
                )
            ]
        )


class SlowScopeAwareMindMemOSClient(FakeMindMemOSClient):
    """Fake client with slow scope-aware memory search."""

    def __init__(self, **kwargs):
        """Initialize fake client configuration and slow memory resource."""
        self.kwargs = kwargs
        self.memory = SlowScopeAwareMindMemOSMemoryResource()


class FakeQueryProvider:
    """Fake planner provider used by query rewrite tests."""

    def __init__(self, rewritten_query: str = "focused tsp 2-opt mutation query"):
        self.rewritten_query = rewritten_query
        self.generate_calls = []

    async def generate(self, prompt, **kwargs):
        """Return a structured rewritten query."""
        self.generate_calls.append({"prompt": prompt, **kwargs})
        schema = kwargs.get("schema")
        parsed = schema(query=self.rewritten_query) if schema else None
        return SimpleNamespace(text=self.rewritten_query, parsed=parsed)


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


@pytest.fixture
def log_messages():
    """Capture loguru messages emitted during a test."""
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(message.record["message"]), level="DEBUG")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


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


def test_mindmemos_client_receives_configured_request_timeout():
    """Pass runtime request timeout through to the MindMemOS client."""
    memory = MindMemOSMemory(
        _config(mindmemos_request_timeout=60),
        client_factory=FakeMindMemOSClient,
    )

    assert memory.request_timeout == 60
    assert memory.client.kwargs["request_timeout"] == 60


def test_mindmemos_client_uses_independent_add_timeout():
    """Use a longer client timeout for slow MindMemOS memory extraction writes."""
    SharedResourceMindMemOSClient.instances = []

    memory = MindMemOSMemory(
        _config(mindmemos_request_timeout=30, mindmemos_add_timeout=120),
        client_factory=SharedResourceMindMemOSClient,
    )

    assert memory.request_timeout == 30
    assert memory.add_timeout == 120
    assert memory.client.kwargs["request_timeout"] == 30
    assert memory.add_client.kwargs["request_timeout"] == 120
    assert len(SharedResourceMindMemOSClient.instances) == 2


def test_mindmemos_zero_timeouts_disable_sdk_timeout():
    """Zero means wait indefinitely instead of falling back to default timeouts."""
    SharedResourceMindMemOSClient.instances = []

    memory = MindMemOSMemory(
        _config(mindmemos_request_timeout=0, mindmemos_add_timeout=0),
        client_factory=SharedResourceMindMemOSClient,
    )

    assert memory.request_timeout == 0
    assert memory.add_timeout == 0
    assert memory.client.kwargs["request_timeout"] is None
    assert memory.add_client.kwargs["request_timeout"] is None


def test_memory_config_exposes_mindmemos_request_timeout_default():
    """Expose MindMemOS runtime request timeout in task YAML config."""
    config = MemoryConfig()

    assert config.mindmemos_request_timeout == 60.0


def test_memory_config_exposes_mindmemos_score_threshold_default():
    """Do not expose an effective MindMemOS score threshold until rerank is enabled."""
    config = MemoryConfig()

    assert config.mindmemos_score_threshold is None


def test_memory_config_exposes_mindmemos_add_timeout_default():
    """Expose a separate timeout for slow MindMemOS memory writes."""
    config = MemoryConfig()

    assert config.mindmemos_add_timeout == 120.0


def test_memory_config_allows_zero_mindmemos_timeouts():
    """Task YAML config should allow zero as the explicit infinite-wait value."""
    config = MemoryConfig(mindmemos_request_timeout=0, mindmemos_add_timeout=0)

    assert config.mindmemos_request_timeout == 0
    assert config.mindmemos_add_timeout == 0


def test_memory_config_exposes_mindmemos_extraction_prompt_language_default():
    """Expose MindMemOS extraction language in task YAML config."""
    config = MemoryConfig()

    assert config.mindmemos_extraction_prompt_language == "auto"
    assert MemoryConfig(mindmemos_extraction_prompt_language="ZH").mindmemos_extraction_prompt_language == "ZH"
    assert MemoryConfig(mindmemos_extraction_prompt_language="EN").mindmemos_extraction_prompt_language == "EN"
    with pytest.raises(ValidationError):
        MemoryConfig(mindmemos_extraction_prompt_language="fr")


@pytest.mark.asyncio
async def test_add_card_emits_task_memory_created_event():
    """Publish a structured event after a task-scope MindMemOS add succeeds."""
    records = []
    sink_id = logger.add(lambda message: records.append(message.record), level="INFO")
    try:
        memory = MindMemOSMemory(_config(), client_factory=FakeMindMemOSClient)
        memory.add_client.memory.add = lambda **kwargs: SimpleNamespace(
            code="ok",
            memories=[SimpleNamespace(id="memory-1")],
        )
        card = MemoryCard(
            id="card-1",
            type=MemoryType.GOOD_ALGORITHM,
            title="Use nearest neighbor",
            content="Seed routes with nearest-neighbor initialization.",
            source="auto",
            generation=2,
        )

        await memory.add_card(card)
    finally:
        logger.remove(sink_id)

    event_records = [
        record
        for record in records
        if record["extra"].get("event_type") == "memory_card_created"
    ]
    assert event_records
    event = event_records[-1]["extra"]
    assert event["scope"] == "task"
    assert event["task_id"] == "task-1"
    assert event["project_id"] == "project-1"
    assert event["memory_id"] == "memory-1"
    assert event["generation"] == 2


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

    call = memory.add_client.memory.add_calls[0]
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
    assert call["metadata"]["custom"] == "value"
    assert "llm4ad_scope" not in call["metadata"]
    assert "project_id" not in call["metadata"]
    assert "task_id" not in call["metadata"]
    assert "session_id" not in call["metadata"]
    assert "card_id" not in call["metadata"]
    assert "card_source" not in call["metadata"]
    assert "prompt_language" not in call


@pytest.mark.asyncio
async def test_add_card_passes_configured_extraction_prompt_language_to_mindmemos_add():
    """Pass configured extraction language to MindMemOS task memory writes."""
    memory = MindMemOSMemory(
        _config(mindmemos_extraction_prompt_language="EN"),
        client_factory=FakeMindMemOSClient,
    )
    card = MemoryCard(
        id="card-1",
        type=MemoryType.GOOD_ALGORITHM,
        title="Use constructive initialization",
        content="Seed the population with nearest-neighbor tours.",
        source="auto",
    )

    await memory.add_card(card)

    assert memory.add_client.memory.add_calls[0]["prompt_language"] == "EN"


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
        "user_id": "user-1",
        "app_id": "llm4ad",
        "agent_id": "project",
        "session_id": "project-1",
        "entity_type": "llm4ad_memory_card",
        "property_name": {
            "in": ["good_algorithm", "error_reflection", "domain_knowledge", "general_insight"]
        }
    }
    assert "# Successful Patterns" in context
    assert "Prefer nearest-neighbor seeds." in context
    assert "# Error Reflections" in context
    assert "Avoid mutating invalid tours." in context
    assert "# Domain Knowledge" in context
    assert "Distances are symmetric." in context


def test_get_prompt_context_injects_structured_hit_metadata():
    """Preserve score/generation/title signals when formatting remote memories."""
    memory = MindMemOSMemory(
        _config(include_user_memory=False, include_project_memory=False, task_memory_limit=5),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.search_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="remote-1",
                memory="Run 2-opt after nearest-neighbor seeding.",
                memory_type="good_algorithm",
                metadata={
                    "title": "2-opt repair",
                    "score": 0.92,
                    "generation": 7,
                    "algorithm_id": "algo-7",
                },
            ),
            SimpleNamespace(
                id="remote-2",
                memory="High mutation rate broke valid tours.",
                memory_type="error_reflection",
                metadata={
                    "title": "Mutation failure",
                    "score": 0.08,
                    "generation": 3,
                },
            ),
        ]
    )

    context = memory.get_prompt_context("tour construction")

    assert "**2-opt repair**" in context
    assert "scope: task" in context
    assert "type: good_algorithm" in context
    assert "score: 0.9200" in context
    assert "gen: 7" in context
    assert "algorithm: algo-7" in context
    assert "Run 2-opt after nearest-neighbor seeding." in context
    assert "**Mutation failure**" in context
    assert "score: 0.0800" in context
    assert "gen: 3" in context


def test_get_prompt_context_surfaces_actionable_evidence_metadata():
    """Prompt formatting should emphasize mechanism/evidence/guidance when present."""
    memory = MindMemOSMemory(
        _config(include_user_memory=False, include_project_memory=False, task_memory_limit=5),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.search_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="remote-1",
                memory="Use nearest-neighbor construction followed by bounded 2-opt.",
                memory_type="good_algorithm",
                metadata={
                    "title": "2-opt repair",
                    "score": -286.13,
                    "generation": 4,
                    "algorithm_id": "algo-good",
                    "evidence": "Improved over parent score -310.50 on clustered TSP.",
                    "applicability": "Clustered and large random TSP instances.",
                    "reuse_guidance": "Cap the 2-opt neighborhood to avoid excessive runtime.",
                },
            )
        ]
    )

    context = memory.get_prompt_context("tour construction")

    assert "Evidence: score=-286.1300, gen=4, algorithm=algo-good" in context
    assert "Mechanism: Use nearest-neighbor construction followed by bounded 2-opt." in context
    assert "Observed evidence: Improved over parent score -310.50 on clustered TSP." in context
    assert "Applicability: Clustered and large random TSP instances." in context
    assert "Reuse guidance: Cap the 2-opt neighborhood to avoid excessive runtime." in context


def test_get_prompt_context_forwards_score_threshold_to_mindmemos_search():
    """Let MindMemOS query/rerank enforce score threshold semantics."""
    memory = MindMemOSMemory(
        _config(
            include_user_memory=False,
            include_project_memory=False,
            task_memory_limit=5,
            mindmemos_rerank=True,
            mindmemos_score_threshold=0.5,
        ),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.search_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="remote-1",
                memory="Use nearest-neighbor seeding before 2-opt.",
                memory_type="good_algorithm",
                metadata={"title": "Valid success", "score": 0.85},
            ),
        ]
    )

    context = memory.get_prompt_context("tour construction")

    assert "Valid success" in context
    search_call = memory.client.memory.search_calls[0]
    assert search_call["rerank"] is True
    assert search_call["score_threshold"] == 0.5


def test_get_prompt_context_omits_score_threshold_when_rerank_is_disabled():
    """Do not send a threshold that MindMemOS ignores without rerank."""
    memory = MindMemOSMemory(
        _config(
            include_user_memory=False,
            include_project_memory=False,
            task_memory_limit=5,
            mindmemos_rerank=False,
            mindmemos_score_threshold=0.5,
        ),
        client_factory=FakeMindMemOSClient,
    )

    memory.get_prompt_context("tour construction")

    search_call = memory.client.memory.search_calls[0]
    assert search_call["rerank"] is False
    assert search_call["score_threshold"] is None


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
async def test_async_prompt_context_rewrites_query_only_for_agentic_search():
    """Use planner provider only to rewrite agentic MindMemOS search queries."""
    provider = FakeQueryProvider()
    memory = MindMemOSMemory(
        _config(
            mindmemos_search_strategy="agentic",
            include_project_memory=False,
            include_user_memory=False,
            task_memory_limit=2,
        ),
        client_factory=FakeMindMemOSClient,
    )
    memory.set_query_provider(provider)
    memory.client.memory.search_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="task-memory",
                memory="Use 2-opt after nearest-neighbor construction.",
                memory_type="good_algorithm",
            )
        ]
    )

    context = await memory.aget_prompt_context(
        query="full raw background that is too broad",
        context={"sampler": "mutation", "parent": "nearest neighbor seed"},
    )

    assert provider.generate_calls
    assert "nearest neighbor seed" in provider.generate_calls[0]["prompt"]
    search_call = memory.client.memory.search_calls[0]
    assert search_call["query"] == "focused tsp 2-opt mutation query"
    assert search_call["search_strategy"] == "agentic"
    assert search_call["agent_id"] == "task"
    assert search_call["session_id"] == "task-1"
    assert search_call["top_k"] == 2
    assert search_call["filters"]["user_id"] == "user-1"
    assert search_call["filters"]["app_id"] == "llm4ad"
    assert search_call["filters"]["agent_id"] == "task"
    assert search_call["filters"]["session_id"] == "task-1"
    assert search_call["filters"]["entity_type"] == "llm4ad_memory_card"
    assert search_call["filters"]["property_name"] == {
        "in": ["good_algorithm", "error_reflection", "domain_knowledge", "general_insight"]
    }
    assert "Use 2-opt" in context


@pytest.mark.asyncio
async def test_async_prompt_context_fast_search_does_not_rewrite_query():
    """Fast mode should not invoke LLM query rewriting."""
    provider = FakeQueryProvider()
    memory = MindMemOSMemory(
        _config(
            mindmemos_search_strategy="fast",
            include_project_memory=False,
            include_user_memory=False,
        ),
        client_factory=FakeMindMemOSClient,
    )
    memory.set_query_provider(provider)

    await memory.aget_prompt_context(
        "tour construction",
        context={
            "sampler": "mutation",
            "parent_score": 0.42,
            "parent_description": "Nearest-neighbor seed followed by 2-opt repair.",
        },
    )

    assert provider.generate_calls == []
    query = memory.client.memory.search_calls[0]["query"]
    assert "tour construction" in query
    assert "sampler: mutation" in query
    assert "parent_score: 0.42" in query
    assert "Nearest-neighbor seed followed by 2-opt repair." in query
    assert memory.client.memory.search_calls[0]["search_strategy"] == "fast"


@pytest.mark.asyncio
async def test_async_prompt_context_searches_task_project_user_with_scope_limits():
    """Remote recall should honor task, project, and user scope identifiers and limits."""
    memory = MindMemOSMemory(
        _config(
            task_memory_limit=1,
            project_memory_limit=2,
            user_memory_limit=3,
        ),
        client_factory=FakeMindMemOSClient,
    )

    await memory.aget_prompt_context("tour construction")

    calls = memory.client.memory.search_calls
    calls_by_agent = {call["agent_id"]: call for call in calls}
    assert set(calls_by_agent) == {"task", "project", "global"}
    assert calls_by_agent["task"]["session_id"] == "task-1"
    assert calls_by_agent["project"]["session_id"] == "project-1"
    assert calls_by_agent["global"]["session_id"] == "global"
    assert calls_by_agent["task"]["top_k"] == 1
    assert calls_by_agent["project"]["top_k"] == 2
    assert calls_by_agent["global"]["top_k"] == 3
    for call in calls:
        assert call["filters"]["user_id"] == "user-1"
        assert call["filters"]["app_id"] == "llm4ad"
        assert call["filters"]["agent_id"] == call["agent_id"]
        assert call["filters"]["session_id"] == call["session_id"]
        assert "llm4ad_scope" not in call["filters"]
        assert "project_id" not in call["filters"]
        assert "task_id" not in call["filters"]


@pytest.mark.asyncio
async def test_async_prompt_context_searches_enabled_scopes_concurrently_but_formats_scope_order():
    """Search enabled scopes concurrently while keeping task, project, user prompt order."""
    memory = MindMemOSMemory(
        _config(
            task_memory_limit=1,
            project_memory_limit=1,
            user_memory_limit=1,
        ),
        client_factory=SlowScopeAwareMindMemOSClient,
    )

    started_at = time.perf_counter()
    context = await memory.aget_prompt_context("tour construction")
    elapsed = time.perf_counter() - started_at

    assert len(memory.client.memory.search_calls) == 3
    assert elapsed < 0.20
    assert context.index("Task scoped nearest-neighbor repair.") < context.index(
        "Project scoped crossover lesson."
    )
    assert context.index("Project scoped crossover lesson.") < context.index(
        "Global mutation lesson."
    )


@pytest.mark.asyncio
async def test_async_prompt_context_logs_mindmemos_usage_without_leaking_content(log_messages):
    """Log MindMemOS retrieval usage without logging query or memory body."""
    provider = FakeQueryProvider()
    memory = MindMemOSMemory(
        _config(
            mindmemos_search_strategy="agentic",
            include_project_memory=False,
            include_user_memory=False,
        ),
        client_factory=FakeMindMemOSClient,
    )
    memory.set_query_provider(provider)
    memory.client.memory.search_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="secret-memory",
                memory="Do not log this retrieved memory body.",
                memory_type="good_algorithm",
            )
        ]
    )

    await memory.aget_prompt_context(
        query="Sensitive raw task background should not be logged.",
        context={"sampler": "mutation"},
    )

    logs = "\n".join(log_messages)
    assert "MindMemOS memory search started" in logs
    assert "sampler=mutation" in logs
    assert "strategy=agentic" in logs
    assert "MindMemOS query rewrite completed" in logs
    assert "MindMemOS scope search completed" in logs
    assert "scope=task" in logs
    assert "hits=1" in logs
    assert "MindMemOS memory injection completed" in logs
    assert "deduped_hits=1" in logs
    assert "injected_chars=" in logs
    assert "Sensitive raw task background should not be logged." not in logs
    assert "Do not log this retrieved memory body." not in logs


@pytest.mark.asyncio
async def test_async_prompt_context_emits_memory_injected_event():
    """Publish structured retrieval stats for task UI observability."""
    records = []
    sink_id = logger.add(lambda message: records.append(message.record), level="INFO")
    try:
        memory = MindMemOSMemory(
            _config(
                include_project_memory=False,
                include_user_memory=False,
            ),
            client_factory=FakeMindMemOSClient,
        )
        memory.client.memory.search_result = SimpleNamespace(
            memories=[
                SimpleNamespace(
                    id="task-memory",
                    memory="Use 2-opt after nearest-neighbor construction.",
                    memory_type="good_algorithm",
                )
            ]
        )

        await memory.aget_prompt_context("tour construction", context={"sampler": "mutation"})
    finally:
        logger.remove(sink_id)

    event_records = [
        record
        for record in records
        if record["extra"].get("event_type") == "mindmemos_memory_injected"
    ]
    assert event_records
    event = event_records[-1]["extra"]
    assert event["sampler"] == "mutation"
    assert event["strategy"] == "fast"
    assert event["scope_hits"] == {"task": 1}
    assert event["deduped_hits"] == 1
    assert event["injected_chars"] > 0
    assert event["task_id"] == "task-1"
    assert event["project_id"] == "project-1"
    assert "used_memories" not in event
    assert "query" not in event
    assert "memory" not in event


@pytest.mark.asyncio
async def test_async_prompt_context_logs_each_scope_limit(log_messages):
    """Log per-scope MindMemOS search limits and result counts."""
    memory = MindMemOSMemory(
        _config(
            task_memory_limit=1,
            project_memory_limit=2,
            user_memory_limit=3,
        ),
        client_factory=FakeMindMemOSClient,
    )

    await memory.aget_prompt_context("tour construction", context={"sampler": "dyca_e1"})

    logs = "\n".join(log_messages)
    assert "sampler=dyca_e1" in logs
    assert "scope=task" in logs
    assert "top_k=1" in logs
    assert "scope=project" in logs
    assert "top_k=2" in logs
    assert "scope=user" in logs
    assert "top_k=3" in logs
    assert "scope_hits={'task': 0, 'project': 0, 'user': 0}" in logs


def test_get_prompt_context_logs_fail_open_search_warning(log_messages):
    """Fail-open search warnings should include scope and fail-open state."""
    memory = MindMemOSMemory(_config(), client_factory=FakeMindMemOSClient)
    memory.client.memory.search_error = RuntimeError("service unavailable")

    memory.get_prompt_context("query")

    logs = "\n".join(log_messages)
    assert "MindMemOS search failed" in logs
    assert "scope=task" in logs
    assert "fail_open=True" in logs
    assert "service unavailable" in logs


@pytest.mark.asyncio
async def test_mindmemos_raw_extractor_keeps_old_thresholds_without_llm_extraction(log_messages):
    """MindMemOS extraction should keep threshold selection but return raw observations."""
    config = SimpleNamespace(
        type="mindmemos_raw_extractor",
        module=None,
        enabled=True,
        extract_good=True,
        extract_bad=True,
        extract_on_failure=True,
        max_cards_per_generation=3,
        good_score_threshold=None,
        bad_score_threshold=None,
        good_relative_threshold=0.5,
        bad_relative_threshold=0.5,
    )
    extractor = create_memory_extractor(provider=SimpleNamespace(), config=config)
    good_algorithm = SimpleNamespace(
        id="algo-good",
        name="Good 2-opt",
        description="Use nearest-neighbor seed and 2-opt local search.",
        score=0.9,
        evaluation=SimpleNamespace(metrics={"gap": 0.1}, error=None),
        is_evaluated=lambda: True,
    )
    bad_algorithm = SimpleNamespace(
        id="algo-bad",
        name="Bad Mutation",
        description="Use very high mutation rate.",
        score=0.1,
        evaluation=SimpleNamespace(metrics={"gap": 0.8}, error=None),
        is_evaluated=lambda: True,
    )

    card = await extractor.extract_from_good(
        good_algorithm,
        [bad_algorithm, good_algorithm],
        generation=4,
        background="TSP benchmark",
    )

    assert card is not None
    assert card.metadata["mindmemos_raw_extraction"] is True
    assert card.metadata["extraction_event"] == "good_algorithm"
    assert card.algorithm_id == "algo-good"
    assert "TSP benchmark" in card.content
    assert "Use nearest-neighbor seed" in card.content
    assert "performed WELL" in card.content
    assert "what key design decisions led to this good performance" in card.content
    assert "patterns or strategies are worth reusing" in card.content
    logs = "\n".join(log_messages)
    assert "MindMemOS raw memory candidate selected" in logs
    assert "event=good_algorithm" in logs
    assert "generation=4" in logs
    assert "algorithm_id=algo-good" in logs


@pytest.mark.asyncio
async def test_mindmemos_raw_extractor_keeps_old_bad_and_failure_prompt_semantics():
    """Bad and failed observations should preserve old avoidance-lesson intent."""
    config = SimpleNamespace(
        type="mindmemos_raw_extractor",
        module=None,
        enabled=True,
        extract_good=True,
        extract_bad=True,
        extract_on_failure=True,
        max_cards_per_generation=3,
        good_score_threshold=None,
        bad_score_threshold=None,
        good_relative_threshold=0.5,
        bad_relative_threshold=0.5,
    )
    extractor = create_memory_extractor(provider=SimpleNamespace(), config=config)
    good_algorithm = SimpleNamespace(
        id="algo-good",
        name="Good 2-opt",
        description="Use nearest-neighbor seed and 2-opt local search.",
        score=0.9,
        evaluation=SimpleNamespace(metrics={"gap": 0.1}, error=None),
        is_evaluated=lambda: True,
    )
    bad_algorithm = SimpleNamespace(
        id="algo-bad",
        name="Bad Mutation",
        description="Use very high mutation rate.",
        score=0.1,
        evaluation=SimpleNamespace(metrics={"gap": 0.8}, error=None),
        is_evaluated=lambda: True,
    )

    bad_card = await extractor.extract_from_bad(
        bad_algorithm,
        [bad_algorithm, good_algorithm],
        generation=5,
        background="TSP benchmark",
    )
    failure_card = await extractor.extract_from_failure(
        bad_algorithm,
        error="IndexError: route index out of range",
        generation=6,
        background="TSP benchmark",
    )

    assert bad_card is not None
    assert bad_card.metadata["extraction_event"] == "error_reflection"
    assert "performed POORLY or failed" in bad_card.content
    assert "what went wrong" in bad_card.content
    assert "pitfalls future algorithm designs should AVOID" in bad_card.content
    assert failure_card is not None
    assert "IndexError: route index out of range" in failure_card.content
    assert "performed POORLY or failed" in failure_card.content
    assert "pitfalls future algorithm designs should AVOID" in failure_card.content


@pytest.mark.asyncio
async def test_mindmemos_raw_extractor_adds_generation_parent_and_code_evidence():
    """Raw observations should give MindMemOS concrete evidence to extract useful cards."""
    config = SimpleNamespace(
        type="mindmemos_raw_extractor",
        module=None,
        enabled=True,
        extract_good=True,
        extract_bad=True,
        extract_on_failure=True,
        max_cards_per_generation=3,
        good_score_threshold=None,
        bad_score_threshold=None,
        good_relative_threshold=0.5,
        bad_relative_threshold=0.5,
    )
    extractor = create_memory_extractor(provider=SimpleNamespace(), config=config)
    good_algorithm = Algorithm(
        id="algo-good",
        insight_type=InsightType.MUTATION,
        name="Good 2-opt",
        description="Use nearest-neighbor seed and 2-opt local search.",
        parent_ids=["parent-1"],
        changed_files=["solver.py"],
        lines_added=12,
        lines_removed=3,
        generation_meta=GenerationMetadata(
            operator="mutation",
            llm_provider="FakeProvider",
            llm_model="fake-model",
            operation_params={
                "parent_score": -310.5,
                "parent_description": "Nearest-neighbor baseline without local repair.",
            },
            targeted_files=["solver.py"],
            change_description="Added bounded 2-opt repair after route construction.",
        ),
        code_artifacts=[
            CodeArtifact(
                file_path="solver.py",
                language="python",
                content="+    tour = two_opt(route, max_swaps=64)\n+    return tour\n",
                content_mode="diff",
                is_entrypoint=True,
            )
        ],
    )
    good_algorithm.set_evaluation_result(-286.13, metrics={"gap": 0.04})
    bad_algorithm = Algorithm(
        id="algo-bad",
        insight_type=InsightType.MUTATION,
        name="Bad Mutation",
        description="Use very high mutation rate.",
    )
    bad_algorithm.set_evaluation_result(-900.0, metrics={"gap": 0.8})

    card = await extractor.extract_from_good(
        good_algorithm,
        [bad_algorithm, good_algorithm],
        generation=4,
        background="TSP benchmark",
    )

    assert card is not None
    assert "Generation evidence:" in card.content
    assert "Sampler/operator: mutation" in card.content
    assert "Parent IDs: parent-1" in card.content
    assert "parent_score: -310.5" in card.content
    assert "Nearest-neighbor baseline without local repair." in card.content
    assert "Changed files: solver.py" in card.content
    assert "Line changes: +12 / -3" in card.content
    assert "Implementation evidence:" in card.content
    assert "File: solver.py" in card.content
    assert "two_opt(route, max_swaps=64)" in card.content
    assert "Do not extract a memory that only says performance improved" in card.content


@pytest.mark.asyncio
async def test_add_card_sends_raw_extraction_observation_without_card_formatting(log_messages):
    """Raw extractor output should be passed to MindMemOS add as source text."""
    memory = MindMemOSMemory(_config(), client_factory=FakeMindMemOSClient)
    card = MemoryCard(
        type=MemoryType.GOOD_ALGORITHM,
        title="Raw good algorithm observation",
        content="Extract reusable LLM4AD algorithm memory from this task observation.",
        source="auto",
        generation=2,
        algorithm_id="algo-raw",
        metadata={
            "mindmemos_raw_extraction": True,
            "extraction_event": "good_algorithm",
        },
    )

    await memory.add_card(card)

    call = memory.add_client.memory.add_calls[0]
    assert call["messages"][0].content == card.content
    assert "Title:" not in call["messages"][0].content
    assert "memory_type" not in call["metadata"]
    assert call["metadata"]["mindmemos_raw_extraction"] is True
    assert call["metadata"]["extraction_event"] == "good_algorithm"
    assert "llm4ad_scope" not in call["metadata"]
    assert "project_id" not in call["metadata"]
    assert "task_id" not in call["metadata"]
    assert "session_id" not in call["metadata"]
    logs = "\n".join(log_messages)
    assert "MindMemOS memory add completed" in logs
    assert "event=good_algorithm" in logs
    assert "scope=task" in logs
    assert "task_id=task-1" in logs


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
    memory.add_client.memory.add_error = RuntimeError("write failed")

    with pytest.raises(RuntimeError, match="write failed"):
        await memory.add_card(
            MemoryCard(
                type=MemoryType.GENERAL_INSIGHT,
                title="General",
                content="Useful memory",
            )
        )
