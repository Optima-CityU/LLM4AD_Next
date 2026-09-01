"""Tests for the MindMemOS memory backend."""

import sys
import time
from types import SimpleNamespace

import pytest
from loguru import logger
from pydantic import ValidationError

from llm4ad.config.memory import MemoryConfig
from llm4ad.planner.base import Algorithm, CodeArtifact, GenerationMetadata, InsightType
from llm4ad.planner.memory import MemoryCard, MemoryType, create_memory, create_memory_extractor
from llm4ad.planner.mindmemos_memory import MindMemOSMemory
from llm4ad.planner.task_memory_selector import create_task_memory_selector


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
        self.presence_result = SimpleNamespace(memories=[SimpleNamespace(id="existing-memory")])
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
        if kwargs.get("page_size") == 1:
            return self.presence_result
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
        """Initialize the deterministic rewritten query and call log."""
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


def test_http_client_list_does_not_double_unwrap_memories():
    """HTTP client list() must return post()'s already-parsed memories, not zero.

    Regression: post() unwraps data.memories into a SimpleNamespace; list()
    previously re-parsed it as a dict, silently dropping every memory.
    """
    from llm4ad.planner.mindmemos_memory import _HttpMindMemOSClient

    client = _HttpMindMemOSClient(
        base_url="http://mindmemos-api:8000",
        api_key="sk-test",
        user_id="user-1",
    )
    # Stub the transport layer the way post() returns it: a SimpleNamespace with
    # an already-parsed .memories list.
    client.post = lambda path, payload: SimpleNamespace(  # type: ignore[method-assign]
        code="ok",
        request_id="req",
        message="",
        memories=[SimpleNamespace(id="card-1"), SimpleNamespace(id="card-2")],
    )

    result = client.memory.list(user_id="user-1", page=1, page_size=50)

    assert [m.id for m in result.memories] == ["card-1", "card-2"]


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


def test_all_scopes_use_the_single_structured_mindmemos_credential():
    """Task, project, and user recall all use the Structured credential."""
    SharedResourceMindMemOSClient.instances = []
    memory = MindMemOSMemory(_config(), client_factory=SharedResourceMindMemOSClient)

    memory._search_remote_scope("task query", 1, "task", "task-1", "task")
    memory._search_remote_scope("project query", 1, "project", "project-1", "project")
    memory._search_remote_scope("user query", 1, "user", "global", "global")

    assert memory.client.kwargs["api_key"] == "sk-test"
    assert [call["query"] for call in memory.client.memory.search_calls] == [
        "task query",
        "project query",
        "user query",
    ]


def test_memory_config_does_not_expose_a_schema_shared_credential():
    """LLM4AD no longer exposes a second Schema data-plane credential."""
    assert "mindmemos_shared_api_key" not in MemoryConfig.model_fields


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

    assert config.mindmemos_request_timeout == 300.0


def test_memory_config_exposes_mindmemos_score_threshold_default():
    """Do not expose an effective MindMemOS score threshold until rerank is enabled."""
    config = MemoryConfig()

    assert config.mindmemos_score_threshold is None


def test_memory_config_exposes_mindmemos_add_timeout_default():
    """Expose a separate timeout for slow MindMemOS memory writes."""
    config = MemoryConfig()

    assert config.mindmemos_add_timeout == 300.0


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
async def test_add_cards_counts_and_logs_structured_operations_without_auxiliary_properties():
    """Count only substantive structured card operations and expose their real action."""
    records = []
    sink_id = logger.add(lambda message: records.append(message.record), level="INFO")
    try:
        memory = MindMemOSMemory(_config(), client_factory=FakeMindMemOSClient)
        memory.add_client.memory.add = lambda **_kwargs: SimpleNamespace(
            code="ok",
            memories=[
                SimpleNamespace(
                    operation="add",
                    memory_id="memory-add",
                    property_name="good_algorithm",
                    source_block_ids=["block-add"],
                ),
                SimpleNamespace(
                    operation="update",
                    memory_id="memory-update",
                    property_name="error_reflection",
                    related_memory_ids=["memory-update-old"],
                    source_block_ids=["block-update"],
                ),
                SimpleNamespace(
                    operation="reinforcement",
                    memory_id="memory-reinforced",
                    property_name="domain_knowledge",
                    source_block_ids=["block-reinforced"],
                ),
                SimpleNamespace(
                    operation="add",
                    memory_id="memory-name",
                    property_name="name",
                    source_block_ids=["block-add"],
                ),
            ],
        )
        cards = [
            MemoryCard(
                id=f"card-{index}",
                type=memory_type,
                title=f"Card {index}",
                content=f"Observation {index}",
                source="auto",
                generation=3,
            )
            for index, memory_type in enumerate(
                [
                    MemoryType.GOOD_ALGORITHM,
                    MemoryType.ERROR_REFLECTION,
                    MemoryType.DOMAIN_KNOWLEDGE,
                ],
                start=1,
            )
        ]

        await memory.add_cards(cards)
    finally:
        logger.remove(sink_id)

    stats = memory.get_stats()
    assert stats["add_count"] == 1
    assert stats["update_count"] == 1
    assert stats["reinforcement_count"] == 1
    operation_events = [
        record["extra"]
        for record in records
        if str(record["extra"].get("event_type", "")).startswith("memory_card_")
    ]
    assert [event["event_type"] for event in operation_events] == [
        "memory_card_created",
        "memory_card_updated",
        "memory_card_reinforced",
    ]
    assert [event["memory_id"] for event in operation_events] == [
        "memory-add",
        "memory-update",
        "memory-reinforced",
    ]


def test_default_structured_client_uses_http_even_when_optional_sdk_is_installed(monkeypatch):
    """Structured reads must not lose property/status metadata through an old SDK model."""
    from llm4ad.planner.mindmemos_memory import _HttpMindMemOSClient

    fake_sdk = type(sys)("mindmemos_sdk")
    fake_sdk.MindMemOSClient = FakeMindMemOSClient
    monkeypatch.setitem(sys.modules, "mindmemos_sdk", fake_sdk)

    memory = MindMemOSMemory(_config())

    assert isinstance(memory.client, _HttpMindMemOSClient)
    assert isinstance(memory.structured_add_client, _HttpMindMemOSClient)


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
    assert call["task_id"] == "task-1"
    assert "messages" not in call
    block = call["document_blocks"][0]
    assert block["block_id"].startswith("llm4ad-task-")
    assert block["messages"][0]["role"] == "user"
    assert "Use constructive initialization" in block["messages"][0]["content"]
    assert "Seed the population" in block["messages"][0]["content"]
    assert call["metadata"]["source"] == "llm4ad"
    assert call["metadata"]["llm4ad_scope"] == "task"
    assert block["metadata"]["memory_type"] == "good_algorithm"
    assert block["metadata"]["structured_allowed_property_names"] == [
        "good_algorithm",
        "name",
        "tags",
    ]
    assert block["metadata"]["score"] == 0.91
    assert block["metadata"]["custom"] == "value"
    assert "project_id" not in call["metadata"]
    assert "task_id" not in call["metadata"]
    assert "session_id" not in call["metadata"]
    assert "card_id" not in call["metadata"]
    assert "card_source" not in call["metadata"]
    assert "prompt_language" not in call


@pytest.mark.asyncio
async def test_add_cards_sends_one_structured_document_batch():
    """Collect one generation of task observations into one structured add."""
    memory = MindMemOSMemory(_config(), client_factory=FakeMindMemOSClient)
    cards = [
        MemoryCard(
            id="good-1",
            type=MemoryType.GOOD_ALGORITHM,
            title="Constructive seed",
            content="Preserve this complete successful algorithm observation.",
            source="auto",
            score=0.91,
            generation=4,
            algorithm_id="algo-good",
            metadata={"mindmemos_raw_extraction": True, "extraction_event": "good_algorithm"},
        ),
        MemoryCard(
            id="bad-1",
            type=MemoryType.ERROR_REFLECTION,
            title="Invalid repair",
            content="Preserve this complete failed algorithm observation.",
            source="auto",
            generation=4,
            algorithm_id="algo-bad",
            metadata={"mindmemos_raw_extraction": True, "extraction_event": "execution_failure"},
        ),
    ]

    await memory.add_cards(cards)

    assert len(memory.add_client.memory.add_calls) == 1
    call = memory.add_client.memory.add_calls[0]
    assert "messages" not in call
    assert call["mode"] == "sync"
    assert call["user_id"] == "user-1"
    assert call["session_id"] == "task-1"
    assert call["task_id"] == "task-1"
    assert call["metadata"]["structured_history_scope"] == "session"
    assert call["idempotency_key"].startswith("llm4ad-task-batch:")
    assert all(
        block["block_id"].startswith("llm4ad-task-")
        for block in call["document_blocks"]
    )
    assert len({block["block_id"] for block in call["document_blocks"]}) == 2
    assert call["document_blocks"][0]["messages"] == [
        {
            "role": "user",
            "content": "Preserve this complete successful algorithm observation.",
        }
    ]
    assert call["document_blocks"][0]["metadata"]["generation"] == 4
    assert call["document_blocks"][0]["metadata"]["algorithm_id"] == "algo-good"
    assert call["document_blocks"][0]["metadata"]["structured_allowed_property_names"] == [
        "good_algorithm",
        "name",
        "tags",
    ]
    assert call["document_blocks"][1]["metadata"]["extraction_event"] == "execution_failure"
    assert call["document_blocks"][1]["metadata"]["structured_allowed_property_names"] == [
        "error_reflection",
        "name",
        "tags",
    ]


def test_memory_config_exposes_mindmemos_context_character_budget():
    config = MemoryConfig()

    assert config.mindmemos_context_char_budget == 20000
    assert MemoryConfig(mindmemos_context_char_budget=12000).mindmemos_context_char_budget == 12000


def test_memory_config_exposes_elite_code_injection_budget():
    config = MemoryConfig()

    assert config.mindmemos_elite_code_slots == 1
    assert config.mindmemos_elite_code_char_budget == 12000


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
        _config(
            mindmemos_agent_id="task",
            include_user_memory=False,
            include_project_memory=False,
        ),
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


def test_list_cards_recovers_task_fields_from_structured_source_provenance():
    """Structured memories keep card fields inside their source document evidence."""
    memory = MindMemOSMemory(
        _config(mindmemos_agent_id="task"),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.list_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="structured-card",
                memory="Bound the candidate neighbourhood to avoid timeouts.",
                memory_type="fact",
                property_name="error_reflection",
                metadata={
                    "source_documents": [
                        {
                            "metadata": {
                                "title": "Bounded neighbourhood",
                                "generation": 7,
                                "algorithm_id": "algo-7",
                                "score": 0.42,
                                "enabled": True,
                                "tags": ["local-search"],
                            }
                        }
                    ]
                },
                status="active",
            )
        ]
    )

    [card] = memory.list_cards()

    assert card.type is MemoryType.ERROR_REFLECTION
    assert card.title == "Bounded neighbourhood"
    assert card.generation == 7
    assert card.algorithm_id == "algo-7"
    assert card.score == 0.42
    assert card.tags == ["local-search"]


def test_list_cards_unwraps_legacy_structured_schema_envelope():
    """Legacy structured wrappers render as ordinary LLM4AD cards."""
    memory = MindMemOSMemory(
        _config(mindmemos_agent_id="task"),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.list_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="wrapped-card",
                memory=repr(
                    {
                        "dynamic_property": {
                            "good_algorithm": "Use increments 5, 3, and 1 for Shell sort.",
                            "tags": "Shell sort, insertion sort",
                        }
                    }
                ),
                property_name="good_algorithm",
                metadata={
                    "source_documents": [
                        {"metadata": {"title": "Shell sort increment strategy"}}
                    ]
                },
                status="active",
            )
        ]
    )

    [card] = memory.list_cards()

    assert card.type is MemoryType.GOOD_ALGORITHM
    assert card.title == "Shell sort increment strategy"
    assert card.content == "Use increments 5, 3, and 1 for Shell sort."
    assert card.tags == ["Shell sort", "insertion sort"]


@pytest.mark.asyncio
async def test_remote_clear_uses_explicit_hard_delete_contract():
    """LLM4AD permanent clear must request physical deletion."""
    memory = MindMemOSMemory(
        _config(mindmemos_allow_remote_clear=True),
        client_factory=FakeMindMemOSClient,
    )

    await memory.delete_card("remote-card")

    assert memory.client.memory.delete_calls == [{"memory_id": "remote-card", "hard": True}]


def test_empty_task_scope_skips_search_after_one_presence_probe():
    """Avoid repeated remote searches for a task scope known to be empty."""
    memory = MindMemOSMemory(
        _config(include_user_memory=False, include_project_memory=False),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.presence_result = SimpleNamespace(memories=[])

    assert memory.get_prompt_context("tour construction") == ""
    assert memory.get_prompt_context("tour construction") == ""

    assert memory.client.memory.search_calls == []
    assert len(memory.client.memory.list_calls) == 1
    probe = memory.client.memory.list_calls[0]
    assert probe["page"] == 1
    assert probe["page_size"] == 1
    assert probe["include_total"] is False
    assert probe["include_inactive"] is False
    assert probe["filters"] == {
        "user_id": "user-1",
        "app_id": "llm4ad",
        "session_id": "task-1",
        "agent_id": "task",
        "entity_type": "llm4ad_memory_card",
        "property_name": {
            "in": ["good_algorithm", "error_reflection", "domain_knowledge", "general_insight"]
        },
    }


@pytest.mark.asyncio
async def test_task_memory_add_marks_an_empty_scope_available_for_search():
    """A successful task-memory write must make later retrieval eligible immediately."""
    memory = MindMemOSMemory(
        _config(
            mindmemos_agent_id="task",
            include_user_memory=False,
            include_project_memory=False,
        ),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.presence_result = SimpleNamespace(memories=[])

    assert memory.get_prompt_context("tour construction") == ""
    await memory.add_card(
        MemoryCard(
            type=MemoryType.GOOD_ALGORITHM,
            title="Nearest-neighbor seed",
            content="Seed routes with nearest-neighbor construction.",
            source="auto",
        )
    )

    memory.client.memory.search_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="remote-1",
                memory="Seed routes with nearest-neighbor construction.",
                memory_type="good_algorithm",
            )
        ]
    )

    context = memory.get_prompt_context("tour construction")

    assert "nearest-neighbor construction" in context
    assert len(memory.client.memory.list_calls) == 1
    assert len(memory.client.memory.search_calls) == 1


@pytest.mark.asyncio
async def test_task_memory_delete_invalidates_scope_presence_cache():
    """Removing task memory must force the next retrieval to re-check the scope."""
    memory = MindMemOSMemory(
        _config(
            mindmemos_agent_id="task",
            include_user_memory=False,
            include_project_memory=False,
            mindmemos_allow_remote_clear=True,
        ),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.presence_result = SimpleNamespace(memories=[])

    await memory.add_card(
        MemoryCard(
            type=MemoryType.GOOD_ALGORITHM,
            title="Nearest-neighbor seed",
            content="Seed routes with nearest-neighbor construction.",
            source="auto",
        )
    )
    await memory.delete_card("remote-1")

    assert memory.get_prompt_context("tour construction") == ""
    assert memory.client.memory.search_calls == []
    assert len(memory.client.memory.list_calls) == 1


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


def test_get_prompt_context_consumes_independent_structured_property_result():
    """Structured search returns the stored property rather than a Schema entity string."""
    memory = MindMemOSMemory(
        _config(include_user_memory=False, include_project_memory=False),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.search_result = SimpleNamespace(
        memories=[
                SimpleNamespace(
                    id="property-hit",
                    memory="Evaluate only the first 16 candidate neighbours.",
                    memory_type="fact",
                    property_name="good_algorithm",
                    entity_type="llm4ad_memory_card",
                    metadata={"title": "Bounded neighbourhood"},
                ),
        ]
    )

    context = memory.get_prompt_context("reduce local-search work")

    assert "# Successful Patterns" in context
    assert "first 16 candidate neighbours" in context


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


def test_get_prompt_context_includes_exact_good_algorithm_source_artifact():
    """A recalled excellent design should expose its implementation to descendants."""
    source = "MODEL_SPEC = {'phase': 0.375, 'rows': [5, 4, 4, 4, 4, 5]}\n"
    memory = MindMemOSMemory(
        _config(
            include_user_memory=False,
            include_project_memory=False,
            task_memory_limit=5,
            mindmemos_context_char_budget=20000,
        ),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.search_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="excellent",
                memory="Use phase-aligned staggered rows.",
                memory_type="good_algorithm",
                metadata={
                    "title": "Phase aligned rows",
                    "structured_content": {
                        "description": "Phase-aligned staggered construction.",
                        "content": ["Reuse the optimized phase as the next search center."],
                        "artifacts": [
                            {
                                "artifact_id": "code-1:model_spec.py",
                                "type": "code",
                                "language": "python",
                                "content": source,
                            }
                        ],
                    },
                },
            )
        ]
    )

    context = memory.get_prompt_context("packing")

    assert "Inherited implementation evidence" in context
    assert source.strip() in context


def test_topk_elite_code_uses_quality_aware_selection_from_wider_recall_pool():
    """The dedicated code lane must not inherit the first lower-quality recall hit."""
    lower_code = "MODEL_SPEC = {'strategy': 'lower_retrieval_match'}\n"
    elite_code = "MODEL_SPEC = {'strategy': 'higher_objective_elite'}\n"
    memory = MindMemOSMemory(
        _config(
            include_user_memory=False,
            include_project_memory=False,
            task_memory_limit=1,
            task_candidate_pool=20,
            task_injection_mode="topk",
            mindmemos_context_char_budget=2000,
            mindmemos_elite_code_slots=1,
            mindmemos_elite_code_char_budget=1000,
        ),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.search_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="closest-lower-score",
                score=0.99,
                memory="The closest semantic match uses a conservative lattice.",
                memory_type="good_algorithm",
                metadata={
                    "title": "Closest lower score",
                    "score": 2.49,
                    "algorithm_id": "algorithm-lower",
                    "structured_content": {
                        "artifacts": [
                            {
                                "artifact_id": "code-1:lower.py",
                                "type": "code",
                                "language": "python",
                                "content": lower_code,
                            }
                        ]
                    },
                },
            ),
            SimpleNamespace(
                id="slightly-less-similar-elite",
                score=0.90,
                memory="A stronger implementation reaches the best measured objective.",
                memory_type="good_algorithm",
                metadata={
                    "title": "Higher objective elite",
                    "score": 2.62,
                    "algorithm_id": "algorithm-elite",
                    "structured_content": {
                        "artifacts": [
                            {
                                "artifact_id": "code-1:elite.py",
                                "type": "code",
                                "language": "python",
                                "content": elite_code,
                            }
                        ]
                    },
                },
            ),
        ]
    )

    context = memory.get_prompt_context("improve circle packing")

    assert "Algorithm ID: algorithm-elite" in context
    assert elite_code.strip() in context
    assert lower_code.strip() not in context


def test_elite_code_selects_best_source_version_inside_merged_memory_card():
    """A merged card should inject one best source version, not every historical version."""
    older_code = "OLD_IMPLEMENTATION = True\n" + "x = 1\n" * 80
    elite_code = "ELITE_IMPLEMENTATION = True\n" + "x = 2\n" * 60
    elite_helper_code = "ELITE_HELPER = 'preserved with the selected version'\n"
    memory = MindMemOSMemory(
        _config(
            include_user_memory=False,
            include_project_memory=False,
            task_memory_limit=1,
            task_injection_mode="topk",
            mindmemos_context_char_budget=1100,
            mindmemos_elite_code_slots=1,
            mindmemos_elite_code_char_budget=900,
        ),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.search_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="merged-elite-card",
                score=0.95,
                memory="A consolidated family of increasingly strong implementations.",
                memory_type="good_algorithm",
                metadata={
                    "title": "Merged implementation family",
                    "source_documents": [
                        {
                            "block_id": "source-old",
                            "metadata": {
                                "algorithm_id": "algorithm-old",
                                "score": 2.49,
                            },
                        },
                        {
                            "block_id": "source-elite",
                            "metadata": {
                                "algorithm_id": "algorithm-elite",
                                "score": 2.62,
                            },
                        },
                    ],
                    "structured_content": {
                        "artifacts": [
                            {
                                "source_block_id": "source-old",
                                "artifact_id": "source-old:code-1:solve.py",
                                "type": "code",
                                "language": "python",
                                "content": older_code,
                            },
                            {
                                "source_block_id": "source-elite",
                                "artifact_id": "source-elite:code-1:solve.py",
                                "type": "code",
                                "language": "python",
                                "content": elite_code,
                            },
                            {
                                "source_block_id": "source-elite",
                                "artifact_id": "source-elite:code-2:helper.py",
                                "type": "code",
                                "language": "python",
                                "content": elite_helper_code,
                            },
                        ]
                    },
                },
            )
        ]
    )

    context = memory.get_prompt_context("improve circle packing")

    assert "Algorithm ID: algorithm-elite" in context
    assert "Objective score: 2.620000" in context
    assert elite_code.strip() in context
    assert elite_helper_code.strip() in context
    assert older_code.strip() not in context
    assert memory.get_stats()["last_elite_code_complete"] is True


def test_success_island_elite_slot_respects_weighted_memory_selection():
    """The code slot must consume the configured selector result, not force the highest score."""
    highest_score_code = "MODEL_SPEC = {'strategy': 'highest_objective_score'}\n"
    weighted_code = (
        "MODEL_SPEC = {'strategy': 'weighted_selector_choice'}\n"
        "WEIGHTED_SOURCE_TAIL = True\n"
    )
    memory = MindMemOSMemory(
        _config(
            include_user_memory=False,
            include_project_memory=False,
            task_memory_limit=5,
            task_candidate_pool=20,
            task_injection_mode="weight",
            mindmemos_context_char_budget=1200,
            mindmemos_elite_code_slots=1,
            mindmemos_elite_code_char_budget=600,
        ),
        client_factory=FakeMindMemOSClient,
    )
    memory._task_selector = create_task_memory_selector(
        "weight",
        {"lambda": 1.0, "seed": 4},
    )
    memory._elite_code_selector = create_task_memory_selector(
        "weight",
        {"lambda": 1.0, "seed": 4},
    )
    memory.client.memory.search_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="first-recall",
                score=0.99,
                memory="Semantically close and highest-scoring implementation.",
                memory_type="good_algorithm",
                metadata={
                    "title": "Highest score",
                    "score": 9.0,
                    "algorithm_id": "algorithm-highest",
                    "structured_content": {
                        "artifacts": [
                            {
                                "artifact_id": "code-1:model_spec.py",
                                "type": "code",
                                "language": "python",
                                "content": highest_score_code,
                            }
                        ]
                    },
                },
            ),
            SimpleNamespace(
                id="best-objective",
                score=0.75,
                memory="A weighted alternative retained for diversity.",
                memory_type="good_algorithm",
                metadata={
                    "title": "Weighted alternative",
                    "score": 2.1,
                    "algorithm_id": "algorithm-weighted",
                    "structured_content": {
                        "artifacts": [
                            {
                                "artifact_id": "code-1:model_spec.py",
                                "type": "code",
                                "language": "python",
                                "content": weighted_code,
                            }
                        ]
                    },
                },
            ),
        ]
    )

    context = memory._build_prompt_context(
        "packing",
        context={"island_strategy": {"memory_policy": "success_only"}},
    )

    assert "# Historical Elite Implementation" in context
    assert "Objective score: 2.100000" in context
    assert weighted_code.strip() in context
    assert highest_score_code.strip() not in context
    assert "WEIGHTED_SOURCE_TAIL = True" in context
    assert "[Memory context truncated]" not in context
    assert len(context) <= 1200


def test_corrective_island_separates_elite_code_from_failure_code_evidence():
    """Successful source is inheritable; failed source is labeled as bounded diagnostic evidence."""
    memory = MindMemOSMemory(
        _config(
            include_user_memory=False,
            include_project_memory=False,
            task_memory_limit=2,
            mindmemos_context_char_budget=2200,
            mindmemos_elite_code_slots=1,
            mindmemos_elite_code_char_budget=800,
        ),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.search_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="good",
                memory="Reuse the feasible parameterization.",
                memory_type="good_algorithm",
                metadata={
                    "title": "Feasible elite",
                    "score": 2.5,
                    "algorithm_id": "good-algorithm",
                    "structured_content": {
                        "artifacts": [
                            {
                                "artifact_id": "code-1:good.py",
                                "type": "code",
                                "language": "python",
                                "content": "GOOD_MODEL = {'feasible': True}\n",
                            }
                        ]
                    },
                },
            ),
            SimpleNamespace(
                id="bad",
                memory="This parameter domain produced no feasible geometry.",
                memory_type="error_reflection",
                metadata={
                    "title": "Infeasible domain",
                    "score": 1.2,
                    "algorithm_id": "bad-algorithm",
                    "structured_content": {
                        "artifacts": [
                            {
                                "artifact_id": "code-1:bad.py",
                                "type": "code",
                                "language": "python",
                                "content": "BAD_MODEL = {'feasible': False}\n",
                            }
                        ]
                    },
                },
            ),
        ]
    )

    context = memory._build_prompt_context(
        "packing",
        context={
            "island_strategy": {
                "memory_policy": "corrective",
                "success_memory_ratio": 0.5,
                "error_memory_ratio": 0.5,
            }
        },
    )

    assert "Historical Elite Implementation" in context
    assert "Objective score: 2.500000" in context
    assert "GOOD_MODEL = {'feasible': True}" in context
    assert "Failure implementation evidence (do not inherit verbatim)" in context
    assert "BAD_MODEL = {'feasible': False}" in context
    assert "objective_score: 1.2000" in context


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
    # Task recall deliberately fetches a wider pool; prompt injection is still
    # capped at task_memory_limit after deduplication/type balancing.
    assert search_call["top_k"] == 8
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
async def test_fast_search_query_keeps_island_generation_and_parent_population_context():
    """Island and population state must change the fast retrieval query across rounds."""
    memory = MindMemOSMemory(
        _config(
            mindmemos_search_strategy="fast",
            include_project_memory=False,
            include_user_memory=False,
        ),
        client_factory=FakeMindMemOSClient,
    )

    await memory.aget_prompt_context(
        "circle packing",
        context={
            "sampler": "summary",
            "generation": 4,
            "island_id": 2,
            "parents": [
                {"score": 0.81, "description": "Hexagonal interior packing"},
                {"score": 0.79, "description": "Boundary-aware radius repair"},
            ],
        },
    )

    query = memory.client.memory.search_calls[0]["query"]
    assert "generation: 4" in query
    assert "island_id: 2" in query
    assert "Hexagonal interior packing" in query
    assert "Boundary-aware radius repair" in query


def test_task_topk_uses_wider_pool_and_keeps_successful_designs_among_many_errors():
    """Repeated errors must not crowd successful algorithm memories out of Top-K."""
    memory = MindMemOSMemory(
        _config(
            include_project_memory=False,
            include_user_memory=False,
            task_memory_limit=5,
            task_candidate_pool=20,
            task_injection_mode="topk",
        ),
        client_factory=FakeMindMemOSClient,
    )
    error_hits = [
        SimpleNamespace(
            id=f"error-{index}",
            memory=f"Repeated invalid-layout failure {index}.",
            memory_type="error_reflection",
        )
        for index in range(1, 6)
    ]
    memory.client.memory.search_result = SimpleNamespace(
        memories=[
            *error_hits,
            SimpleNamespace(
                id="successful-design",
                memory="Reuse the boundary-aware hexagonal packing layout.",
                memory_type="good_algorithm",
            ),
            SimpleNamespace(
                id="domain-constraint",
                memory="Every circle must remain inside the unit square.",
                memory_type="domain_knowledge",
            ),
        ]
    )

    context = memory.get_prompt_context("improve circle packing")

    assert memory.client.memory.search_calls[0]["top_k"] == 20
    assert "Reuse the boundary-aware hexagonal packing layout." in context
    assert "Every circle must remain inside the unit square." in context
    assert sum(text in context for text in [hit.memory for hit in error_hits]) == 3


def test_task_candidates_deduplicate_identical_memory_content_before_injection():
    """Legacy duplicate rows should consume only one prompt slot."""
    memory = MindMemOSMemory(
        _config(
            include_project_memory=False,
            include_user_memory=False,
            task_memory_limit=3,
            task_candidate_pool=12,
            task_injection_mode="topk",
        ),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.search_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="duplicate-error-1",
                memory="Reject layouts with overlapping circles.",
                memory_type="error_reflection",
            ),
            SimpleNamespace(
                id="duplicate-error-2",
                memory="  reject   layouts with OVERLAPPING circles.  ",
                memory_type="error_reflection",
            ),
            SimpleNamespace(
                id="successful-design",
                memory="Reuse a feasible hexagonal seed.",
                memory_type="good_algorithm",
            ),
            SimpleNamespace(
                id="domain-constraint",
                memory="Respect square boundary constraints.",
                memory_type="domain_knowledge",
            ),
        ]
    )

    context = memory.get_prompt_context("improve circle packing")

    assert context.lower().count("reject layouts with overlapping circles.") == 1
    assert "Reuse a feasible hexagonal seed." in context
    assert "Respect square boundary constraints." in context


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
    # Task scope fetches a wider candidate pool before applying its injection
    # limit, while shared scopes still request their final configured limits.
    assert calls_by_agent["task"]["top_k"] == 4
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
    assert "[long-term memory] retrieval started" in logs
    assert "sampler=mutation" in logs
    assert "search_strategy=agentic" in logs
    assert "[long-term memory] query rewrite completed" in logs
    assert "[long-term memory] scope search completed" in logs
    assert "scope=task" in logs
    assert "hits=1" in logs
    assert "[long-term memory] injection completed" in logs
    assert "task_injection=topk" in logs
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
    assert "[long-term memory] scope search failed" in logs
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
    assert "[long-term memory] extracted candidate" in logs
    assert "type=good algorithm" in logs
    assert "generation=4" in logs
    assert "algorithm=algo-good" in logs


@pytest.mark.asyncio
async def test_mindmemos_raw_extractor_preserves_bad_and_distinguishes_execution_failure_prompts():
    """Execution failures must not be interpreted as low or zero scores."""
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
    assert failure_card.metadata["extraction_event"] == "execution_failure"
    assert "IndexError: route index out of range" in failure_card.content
    assert "Evaluation execution failed before a valid score was produced" in failure_card.content
    assert "Do not infer that this algorithm scored zero or performed poorly" in failure_card.content
    assert "Do not claim a score or comparative performance" in failure_card.content
    assert "performed POORLY or failed" not in failure_card.content


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
async def test_raw_algorithm_code_is_sent_as_complete_independent_source_artifact():
    """Long source code must be preserved separately instead of storing a truncated artifact."""
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
    full_source = "def solve():\n" + "    value += 1\n" * 200 + "    return 'FULL_SOURCE_TAIL'\n"
    algorithm = Algorithm(
        id="algo-full-source",
        insight_type=InsightType.MUTATION,
        name="Full source evidence",
        description="Keep the complete implementation as immutable evidence.",
        code_artifacts=[
            CodeArtifact(
                file_path="solver.py",
                language="python",
                content=full_source,
                content_mode="full",
                is_entrypoint=True,
            )
        ],
    )
    algorithm.set_evaluation_result(1.0, metrics={"validity": 1.0})
    card = await extractor.extract_from_good(
        algorithm,
        [algorithm],
        generation=1,
        background="Evidence preservation",
    )
    assert card is not None

    memory = MindMemOSMemory(_config(), client_factory=FakeMindMemOSClient)
    await memory.add_card(card)

    block = memory.add_client.memory.add_calls[0]["document_blocks"][0]
    assert block["source_artifacts"] == [
        {
            "artifact_id": "code-1:solver.py",
            "type": "code",
            "language": "python",
            "content": full_source,
        }
    ]
    assert full_source not in block["messages"][0]["content"]
    assert "[truncated]" not in block["messages"][0]["content"]
    assert "_mindmemos_source_artifacts" not in block["metadata"]


@pytest.mark.asyncio
async def test_solver_candidate_memory_focuses_on_formulas_constraints_and_solver_evidence():
    """Structured candidates should produce reusable modeling knowledge, not code trivia."""
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
    model_spec = "MODEL_SPEC = {'groups': [{'count': 26, 'x': 'cx + dx * i', 'y': 'cy'}]}\n"
    algorithm = Algorithm(
        id="solver-formula",
        insight_type=InsightType.MUTATION,
        name="Expression candidate",
        description="Use a parameterized center construction.",
        code_artifacts=[
            CodeArtifact(
                file_path="model_spec.py",
                language="python",
                content=model_spec,
                content_mode="full",
            )
        ],
    )
    algorithm.set_evaluation_result(
        2.4,
        metrics={
            "sum_radii": 2.4,
            "validity": 1.0,
            "solver_gap": 0.0,
            "solver_nodes": 1.0,
        },
    )

    card = await extractor.extract_from_good(
        algorithm,
        [algorithm],
        generation=2,
        background="Solver-assisted mathematical optimization",
    )

    assert card is not None
    assert "Structured mathematical candidate" in card.content
    assert "formula families, parameterization, structural constraints, or symmetry" in card.content
    assert "solver_gap: 0.0" in card.content
    assert card.metadata["_mindmemos_source_artifacts"] == [
        {
            "artifact_id": "code-1:model_spec.py",
            "type": "code",
            "language": "python",
            "content": model_spec,
        }
    ]


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
    block = call["document_blocks"][0]
    assert block["messages"][0]["content"] == card.content
    assert "Title:" not in block["messages"][0]["content"]
    assert block["metadata"]["memory_type"] == "good_algorithm"
    assert block["metadata"]["structured_allowed_property_names"] == [
        "good_algorithm",
        "name",
        "tags",
    ]
    assert block["metadata"]["mindmemos_raw_extraction"] is True
    assert block["metadata"]["extraction_event"] == "good_algorithm"
    assert call["metadata"]["llm4ad_scope"] == "task"
    assert "project_id" not in call["metadata"]
    assert "task_id" not in call["metadata"]
    assert "session_id" not in call["metadata"]
    logs = "\n".join(log_messages)
    assert "[long-term memory] inserted structured task-memory batch" in logs
    assert "cards=1" in logs
    assert "task=task-1" in logs


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


def test_manual_mode_fetches_pinned_cards_even_when_limit_zero():
    """Manual mode injects pinned shared cards regardless of legacy scope settings."""
    memory = MindMemOSMemory(
        _config(
            retrieval_mode="manual",
            pinned_card_ids=["card-1"],
            # Manual mode owns shared-scope selection. Older tasks may retain
            # the default false flags, so those flags must not suppress pins.
            include_user_memory=False,
            include_project_memory=False,
            include_task_memory=False,
            user_memory_limit=0,
            project_memory_limit=0,
        ),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.list_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="card-1",
                memory="Pinned shared insight.",
                memory_type="good_algorithm",
                metadata={},
            )
        ]
    )

    context = memory.get_prompt_context("tour construction")

    # Shared scopes are listed (pinned) rather than searched.
    listed_agents = {call.get("agent_id") for call in memory.client.memory.list_calls}
    assert listed_agents == {"project", "global"}
    assert memory.client.memory.search_calls == []
    assert "Pinned shared insight." in context
    # The list call must carry the card filters so only card-content rows are
    # returned (the bug that caused pinned ids to be "not found").
    for call in memory.client.memory.list_calls:
        filters = call.get("filters") or {}
        assert filters.get("entity_type") == "llm4ad_memory_card"
        assert "property_name" in filters
        assert call.get("include_inactive") is True


def test_manual_mode_does_not_inject_disabled_pinned_cards():
    """Archived or metadata-disabled pins must never reach prompt context."""
    memory = MindMemOSMemory(
        _config(
            retrieval_mode="manual",
            pinned_card_ids=["active-card", "archived-card", "metadata-disabled-card"],
            include_task_memory=False,
        ),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.list_result = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="active-card",
                memory="Active pinned insight.",
                memory_type="good_algorithm",
                status="active",
                metadata={},
            ),
            SimpleNamespace(
                id="archived-card",
                memory="Archived pinned insight.",
                memory_type="good_algorithm",
                status="archived",
                metadata={},
            ),
            SimpleNamespace(
                id="metadata-disabled-card",
                memory="Disabled pinned insight.",
                memory_type="good_algorithm",
                status="active",
                metadata={"enabled": False},
            ),
        ]
    )

    context = memory.get_prompt_context("tour construction")

    assert "Active pinned insight." in context
    assert "Archived pinned insight." not in context
    assert "Disabled pinned insight." not in context


def test_manual_mode_logs_pinned_fetch_not_search(log_messages):
    """Manual mode logs a pinned-fetch line, never a search/top_k line."""
    memory = MindMemOSMemory(
        _config(
            retrieval_mode="manual",
            pinned_card_ids=["card-1"],
            include_user_memory=True,
            include_project_memory=False,
            include_task_memory=False,
        ),
        client_factory=FakeMindMemOSClient,
    )
    memory.client.memory.list_result = SimpleNamespace(
        memories=[
            SimpleNamespace(id="card-1", memory="Pinned insight.", memory_type="good_algorithm", metadata={})
        ]
    )

    memory.get_prompt_context("query")

    logs = "\n".join(log_messages)
    assert "scope pinned-fetch completed" in logs
    assert "retrieval=manual-pinned" in logs
    # The pinned shared scope must not be logged as a search with top_k.
    assert "scope=user top_k" not in logs


def test_manual_mode_with_no_pinned_cards_injects_nothing():
    """Manual mode with an empty pinned set injects no shared memory."""
    memory = MindMemOSMemory(
        _config(
            retrieval_mode="manual",
            pinned_card_ids=[],
            include_user_memory=True,
            include_project_memory=True,
            include_task_memory=False,
        ),
        client_factory=FakeMindMemOSClient,
    )

    context = memory.get_prompt_context("tour construction")

    assert context == ""
    assert memory.client.memory.list_calls == []
    assert memory.client.memory.search_calls == []


def test_manual_mode_seeds_and_rereads_pinned_file(tmp_path):
    """Pinned ids seed a runtime file that is re-read on each injection."""
    from llm4ad.planner.mindmemos_memory import PINNED_MEMORY_FILENAME, _write_pinned_file

    memory = MindMemOSMemory(
        _config(
            retrieval_mode="manual",
            pinned_card_ids=["card-1"],
            include_user_memory=True,
            include_project_memory=False,
            include_task_memory=False,
        ),
        client_factory=FakeMindMemOSClient,
    )
    memory.set_memory_dir(tmp_path)

    # set_memory_dir seeds the file from the config snapshot.
    pinned_file = tmp_path / PINNED_MEMORY_FILENAME
    assert pinned_file.exists()
    assert memory._current_pinned_ids() == ["card-1"]

    # A runtime edit to the file is picked up without touching the config.
    _write_pinned_file(pinned_file, ["card-2", "card-3"])
    assert memory._current_pinned_ids() == ["card-2", "card-3"]


def test_pinned_file_reseeds_from_config_on_rerun(tmp_path):
    """Each run start re-seeds the pinned file from config (rerun picks up changes)."""
    from llm4ad.planner.mindmemos_memory import PINNED_MEMORY_FILENAME, _write_pinned_file

    pinned_file = tmp_path / PINNED_MEMORY_FILENAME
    # Simulate a stale file from a previous run.
    _write_pinned_file(pinned_file, ["stale-1"])

    memory = MindMemOSMemory(
        _config(retrieval_mode="manual", pinned_card_ids=["config-1"]),
        client_factory=FakeMindMemOSClient,
    )
    memory.set_memory_dir(tmp_path)

    # Rerun re-seeds from the (new) config, overwriting the stale file.
    assert memory._current_pinned_ids() == ["config-1"]


def test_auto_mode_does_not_write_pinned_file(tmp_path):
    """Auto retrieval mode injects by search and writes no pinned file."""
    from llm4ad.planner.mindmemos_memory import PINNED_MEMORY_FILENAME

    memory = MindMemOSMemory(
        _config(retrieval_mode="auto"),
        client_factory=FakeMindMemOSClient,
    )
    memory.set_memory_dir(tmp_path)

    assert not (tmp_path / PINNED_MEMORY_FILENAME).exists()
