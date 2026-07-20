"""Tests for task-scoped memory card management."""

import uuid

import anyio
import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app import models
from app.core.db import engine
from app.schemas.memory import (
    MemoryCardExtractionCommitRequest,
    MemoryCardExtractionRequest,
    MemoryCardResponse,
    MemoryCardUpsertRequest,
    TaskMemoryPromotionRequest,
)
from app.services import memory_service
from tests.utils.user import create_random_user


class _FakeMindMemOSStreamResponse:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeMindMemOSStreamContext:
    def __init__(self, response: _FakeMindMemOSStreamResponse):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *_args):
        return None


@pytest.fixture(scope="module")
def db():
    with Session(engine) as session:
        yield session


def _create_task_for_user(db: Session, user_id: uuid.UUID) -> models.Task:
    project = models.Project(name="Memory Project", description="", user_id=user_id)
    db.add(project)
    db.commit()
    db.refresh(project)

    task = models.Task(
        name="Memory Task",
        project_id=project.id,
        input_args={
            "memory": {
                "type": "local_yaml",
                "static_cards": [
                    {
                        "id": "existing-card",
                        "type": "domain_knowledge",
                        "title": "Symmetric distances",
                        "content": "TSP distances are symmetric.",
                        "enabled": True,
                        "tags": ["tsp"],
                    }
                ],
            }
        },
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _fake_mindmemos(
    monkeypatch: pytest.MonkeyPatch,
    initial: dict[str, str] | None = None,
    initial_metadata: dict[str, dict] | None = None,
):
    store = dict(initial or {})
    statuses = {memory_id: "active" for memory_id in store}
    metadata_by_id: dict[str, dict] = {
        memory_id: dict((initial_metadata or {}).get(memory_id) or {})
        for memory_id in store
    }
    calls: list[tuple[str, dict]] = []
    counter = 0

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        nonlocal counter
        calls.append((path, payload))
        if path == "/v1/memory/list":
            requested_ids = set((payload.get("filters") or {}).get("memory_id", {}).get("in", store.keys()))
            return {
                "code": "ok",
                "data": {
                    "memories": [
                        {
                            "id": memory_id,
                            "memory": content,
                            "status": statuses.get(memory_id, "active"),
                            "mem_type": "fact",
                            "property_name": "general_insight",
                            "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                            "entity_id": memory_id,
                            "metadata": {
                                "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                                "entity_id": memory_id,
                                **metadata_by_id.get(memory_id, {}),
                            },
                        }
                        for memory_id, content in store.items()
                        if memory_id in requested_ids
                    ],
                    "page": payload.get("page", 1),
                    "page_size": payload.get("page_size", 20),
                    "total": len(store),
                    "has_more": False,
                },
            }
        if path == "/v1/memory/add":
            counter += 1
            memory_id = f"remote-card-{counter}"
            store[memory_id] = payload["messages"][0]["content"]
            statuses[memory_id] = "active"
            metadata_by_id[memory_id] = dict(payload.get("metadata") or {})
            return {
                "code": "ok",
                "data": {
                    "memories": [
                        {
                            "operation": "add",
                            "memory_id": memory_id,
                            "content": store[memory_id],
                            "memory_type": "fact",
                            "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                            "entity_id": memory_id,
                            "property_name": "good_algorithm",
                        }
                    ]
                },
            }
        if path == "/v1/memory/update":
            if "content" in payload:
                store[payload["memory_id"]] = payload["content"]
            statuses[payload["memory_id"]] = payload.get("status", "active")
            metadata_by_id.setdefault(payload["memory_id"], {}).update(payload.get("metadata_patch") or {})
            return {"code": "ok", "data": None}
        if path == "/v1/memory/delete":
            assert payload["hard"] is True
            store.pop(payload["memory_id"], None)
            statuses.pop(payload["memory_id"], None)
            metadata_by_id.pop(payload["memory_id"], None)
            return {"code": "ok", "data": None}
        raise AssertionError(f"unexpected MindMemOS path: {path}")

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)
    monkeypatch.setattr(memory_service, "_ensure_mindmemos_provider_binding", lambda db, current_user: None)
    return store, calls


def _enable_system_mindmemos(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_ENABLED", True)
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_JWT_SECRET", "jwt-test-secret")


def test_mindmemos_stream_add_uses_add_timeout(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, float] = {}

    class FakeAsyncClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return _FakeMindMemOSStreamContext(
                _FakeMindMemOSStreamResponse(
                    [
                        "event: completed",
                        'data: {"data": {"memories": []}}',
                        "",
                    ]
                )
            )

    monkeypatch.setattr(memory_service.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_ADD_TIMEOUT", 120.0)
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_REQUEST_TIMEOUT", 10.0)

    async def collect():
        return [
            event
            async for event in memory_service._mindmemos_stream_post(
                models.User(id=uuid.uuid4(), email="stream-timeout@example.com", hashed_password="x"),
                "/v1/memory/add/stream",
                {},
                scopes=["memory:write"],
            )
        ]

    events = anyio.run(collect)

    assert captured["timeout"] == 120.0
    assert events[-1]["event"] == "completed"


def test_mindmemos_stream_add_treats_zero_timeout_as_infinite(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, float | None] = {}

    class FakeAsyncClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return _FakeMindMemOSStreamContext(
                _FakeMindMemOSStreamResponse(
                    [
                        "event: completed",
                        'data: {"data": {"memories": []}}',
                        "",
                    ]
                )
            )

    monkeypatch.setattr(memory_service.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_ADD_TIMEOUT", 0.0)
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_REQUEST_TIMEOUT", 10.0)

    async def collect():
        return [
            event
            async for event in memory_service._mindmemos_stream_post(
                models.User(id=uuid.uuid4(), email="stream-zero-timeout@example.com", hashed_password="x"),
                "/v1/memory/add/stream",
                {},
                scopes=["memory:write"],
            )
        ]

    events = anyio.run(collect)

    assert captured["timeout"] is None
    assert events[-1]["event"] == "completed"


@pytest.mark.asyncio
async def test_mindmemos_stream_post_emits_heartbeat_during_idle(monkeypatch: pytest.MonkeyPatch):
    class FakeAsyncClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            class SlowResponse(_FakeMindMemOSStreamResponse):
                async def aiter_lines(self):
                    await anyio.sleep(0.05)
                    for line in [
                        "event: completed",
                        'data: {"data": {"memories": []}}',
                        "",
                    ]:
                        yield line

            return _FakeMindMemOSStreamContext(SlowResponse([]))

    monkeypatch.setattr(memory_service.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(memory_service, "MEMORY_STREAM_HEARTBEAT_SECONDS", 0.01)

    events = [
        event
        async for event in memory_service._mindmemos_stream_post(
            models.User(id=uuid.uuid4(), email="stream-heartbeat@example.com", hashed_password="x"),
            "/v1/memory/add/stream",
            {},
            scopes=["memory:write"],
        )
    ]

    assert any(event["event"] == "heartbeat" for event in events)
    assert events[-1]["event"] == "completed"


def _mark_user_memory_bound(db: Session, user_id: uuid.UUID) -> None:
    config = db.exec(
        select(models.UserMemoryConfig).where(models.UserMemoryConfig.user_id == user_id)
    ).first()
    if config is None:
        config = models.UserMemoryConfig(user_id=user_id)
    config.mindmemos_binding_id = "pb_test"
    db.add(config)
    db.commit()


def _assert_task_scope_filters(filters: dict, user: models.User, task: models.Task, memory_ids: list[str]) -> None:
    assert filters["memory_id"] == {"in": memory_ids}
    assert filters["user_id"] == str(user.id)
    assert filters["app_id"] == memory_service.settings.LLM4AD_MINDMEMOS_APP_ID
    assert filters["session_id"] == str(task.id)
    assert filters["agent_id"] == "task"
    assert "llm4ad_scope" not in filters
    assert "project_id" not in filters
    assert "task_id" not in filters


def test_task_memory_scope_uses_root_task_for_child_versions(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    root_task = _create_task_for_user(db, user.id)
    child_task = models.Task(
        name="Memory Task Child",
        project_id=root_task.project_id,
        group_id=root_task.id,
        parent_id=root_task.id,
        input_args=root_task.input_args,
    )
    db.add(child_task)
    db.commit()
    db.refresh(child_task)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    calls: list[dict] = []

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        assert path == "/v1/memory/list"
        calls.append(payload)
        return {
            "code": "ok",
            "data": {
                "memories": [],
                "page": payload.get("page", 1),
                "page_size": payload.get("page_size", 20),
                "total": 0,
                "has_more": False,
            },
        }

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)
    monkeypatch.setattr(memory_service, "_ensure_mindmemos_provider_binding", lambda db, current_user: None)

    memory_service.list_memory_cards_page(
        db,
        user,
        scope="task",
        task_id=child_task.id,
        page=1,
        page_size=20,
    )

    assert calls[0]["session_id"] == str(root_task.id)
    assert calls[0]["filters"]["session_id"] == str(root_task.id)
    assert calls[0]["agent_id"] == "task"


def test_task_memory_crud_uses_mindmemos_when_enabled(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    _store, calls = _fake_mindmemos(
        monkeypatch,
        {
            "existing-card": "TSP distances are symmetric."
        },
    )

    cards = memory_service.list_task_memory_cards(db, task.id, user)
    assert [card.id for card in cards.items] == ["existing-card"]

    created = memory_service.upsert_task_memory_card(
        db,
        task.id,
        user,
        MemoryCardUpsertRequest(
            type="good_algorithm",
            title="Nearest seed",
            content="Seed tours with nearest-neighbor construction.",
            enabled=True,
            tags=["construction"],
        ),
    )
    assert created.id
    assert created.source == "mindmemos"

    updated = memory_service.upsert_task_memory_card(
        db,
        task.id,
        user,
        MemoryCardUpsertRequest(
            id=created.id,
            type="good_algorithm",
            title="Nearest plus 2-opt",
            content="Seed tours and then run 2-opt.",
            enabled=False,
            tags=["construction", "local-search"],
        ),
    )
    assert updated.enabled is False
    assert updated.title == "Nearest plus 2-opt"
    update_payload = [payload for path, payload in calls if path == "/v1/memory/update"][-1]
    assert update_payload["status"] == "archived"
    assert update_payload["user_id"] == str(user.id)
    assert update_payload["app_id"] == memory_service.settings.LLM4AD_MINDMEMOS_APP_ID
    assert update_payload["agent_id"] == "task"
    assert update_payload["session_id"] == str(task.id)

    cards = memory_service.list_task_memory_cards(db, task.id, user)
    assert len(cards.items) == 2
    assert {card.id for card in cards.items} == {"existing-card", created.id}
    assert {card.id: card.enabled for card in cards.items}[created.id] is False

    memory_service.delete_task_memory_card(db, task.id, user, created.id)
    assert [card.id for card in memory_service.list_task_memory_cards(db, task.id, user).items] == [
        "existing-card"
    ]


def test_filter_active_task_pinned_memory_ids_excludes_disabled_cards(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    _fake_mindmemos(
        monkeypatch,
        initial={
            "enabled-card": "An active shared memory.",
            "disabled-card": "A disabled shared memory.",
        },
        initial_metadata={
            "enabled-card": {"enabled": True},
            "disabled-card": {"enabled": False},
        },
    )

    pinned_ids = memory_service.filter_active_task_pinned_memory_ids(
        db,
        current_user=user,
        task_id=task.id,
        pinned_card_ids=["enabled-card", "disabled-card", "missing-card"],
    )

    assert pinned_ids == ["enabled-card"]


def test_task_memory_update_preserves_remote_id(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    _fake_mindmemos(
        monkeypatch,
        {"remote-existing": "Existing MindMemOS content."},
    )

    updated = memory_service.upsert_task_memory_card(
        db,
        task.id,
        user,
        MemoryCardUpsertRequest(
            id="remote-existing",
            type="general_insight",
            title="Updated remote card",
            content="The same card should be updated, not duplicated.",
            enabled=True,
        ),
    )

    cards = memory_service.list_task_memory_cards(db, task.id, user)
    assert updated.id == "remote-existing"
    assert len(cards.items) == 1
    assert cards.items[0].content == "The same card should be updated, not duplicated."


def test_task_memory_status_update_does_not_touch_content_or_require_model_binding(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _store, calls = _fake_mindmemos(
        monkeypatch,
        {"remote-existing": "Existing MindMemOS content."},
    )
    monkeypatch.setattr(
        memory_service,
        "_ensure_mindmemos_provider_binding",
        lambda db, current_user: (_ for _ in ()).throw(AssertionError("provider binding should not be checked")),
    )

    updated = memory_service.update_task_memory_card_status(
        db,
        task.id,
        user,
        "remote-existing",
        enabled=False,
    )

    update_payload = [payload for path, payload in calls if path == "/v1/memory/update"][-1]
    assert "content" not in update_payload
    assert update_payload["status"] == "archived"
    assert update_payload["metadata_patch"]["enabled"] is False
    assert _store["remote-existing"] == "Existing MindMemOS content."
    assert updated.id == "remote-existing"
    assert updated.enabled is False
    assert updated.content == "Existing MindMemOS content."


def test_task_memory_delete_requires_card_in_current_scope(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _store, calls = _fake_mindmemos(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        memory_service.delete_task_memory_card(db, task.id, user, "other-scope-card")

    assert exc.value.status_code == 404
    assert not [payload for path, payload in calls if path == "/v1/memory/delete"]


def test_memory_card_response_splits_editable_and_readonly_fields():
    card = memory_service._remote_memory_to_card(  # noqa: SLF001
        {
            "id": "remote-structured",
            "memory": "2026-07-09 should remain content only when MindMemOS returned it.",
            "status": "active",
            "last_update_at": "2026-07-09 10:20:30",
            "event_time": "2026-07-08 09:00:00",
            "source_timestamp": "2026-07-08 09:00:00",
            "property_name": "good_algorithm",
            "metadata": {
                "entity_name": "TSP operator guidance",
                "property_time": "2026-07-08",
                "memory_type": "good_algorithm",
                "title": "",
                "tags": ["tsp"],
                "score": 0.87,
                "generation": 6,
                "algorithm_id": "algo-6",
                "content_hash": "internal-hash",
            },
        }
    )

    assert card.title == "TSP operator guidance"
    assert card.content == "2026-07-09 should remain content only when MindMemOS returned it."
    assert card.type == "good_algorithm"
    assert card.tags == ["tsp"]
    assert card.score == 0.87
    assert card.generation == 6
    assert card.algorithm_id == "algo-6"
    assert card.readonly.status == "active"
    assert card.readonly.entity_name == "TSP operator guidance"
    assert card.readonly.property_name == "good_algorithm"
    assert card.readonly.property_time == "2026-07-08"
    assert card.readonly.last_update_at == "2026-07-09 10:20:30"
    assert card.readonly.event_time == "2026-07-08 09:00:00"
    assert card.readonly.source_timestamp == "2026-07-08 09:00:00"


def test_memory_card_response_does_not_synthesize_title_from_content():
    card = memory_service._remote_memory_to_card(  # noqa: SLF001
        {
            "id": "remote-untitled",
            "memory": "First line should stay content, not become title.",
            "status": "active",
            "metadata": {},
        }
    )

    assert card.title == "未命名记忆"


def test_remote_list_merges_schema_tags_without_rendering_tag_rows(monkeypatch: pytest.MonkeyPatch):
    user = models.User(id=uuid.uuid4(), email="tags@example.com", hashed_password="x")
    calls: list[tuple[str, dict]] = []

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        calls.append((path, payload))
        assert path == "/v1/memory/list"
        return {
            "code": "ok",
            "data": {
                "memories": [
                    {
                        "id": "card-1",
                        "memory": "Run 2-opt after nearest-neighbor seeding.",
                        "status": "active",
                        "property_name": "good_algorithm",
                        "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                        "entity_id": "entity-1",
                        "metadata": {"entity_name": "TSP local search"},
                    },
                    {
                        "id": "tags-1",
                        "memory": "TSP, local-search, 2-opt",
                        "status": "active",
                        "property_name": "tags",
                        "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                        "entity_id": "entity-1",
                        "metadata": {"entity_name": "TSP local search"},
                    },
                ],
                "page": 1,
                "page_size": 20,
                "total": 2,
                "has_more": False,
            },
        }

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)

    page = memory_service._remote_list_cards(  # noqa: SLF001
        user,
        {"user_id": str(user.id), "app_id": "llm4ad", "agent_id": "global", "session_id": "global"},
        page=1,
        page_size=20,
    )

    assert len(page.items) == 1
    assert page.items[0].id == "card-1"
    assert page.items[0].tags == ["TSP", "local-search", "2-opt"]
    assert [payload.get("filters") for _path, payload in calls] == [
        {
            "user_id": str(user.id),
            "app_id": "llm4ad",
            "session_id": "global",
            "agent_id": "global",
            "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
            "property_name": {
                "in": [
                    "good_algorithm",
                    "error_reflection",
                    "domain_knowledge",
                    "general_insight",
                ]
            },
        },
        {
            "user_id": str(user.id),
            "app_id": "llm4ad",
            "session_id": "global",
            "agent_id": "global",
            "entity_id": {"in": ["entity-1"]},
            "property_name": "tags",
        },
    ]


@pytest.mark.asyncio
async def test_stream_extract_memory_cards_emits_progress_and_completed_cards(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    progress_payloads: list[dict] = []

    async def fake_stream(_current_user, path: str, payload: dict, *, scopes: list[str]):
        assert path == "/v1/memory/add/stream"
        assert payload["messages"][0]["content"] == "Use 2-opt after nearest-neighbor construction."
        progress_payloads.append(payload)
        yield {
            "event": "progress",
            "stage": "llm_extracting",
            "message": "正在提取记忆",
            "percent": 45,
        }
        yield {
            "event": "completed",
            "stage": "completed",
            "message": "done",
            "data": {
                "memories": [
                    {
                        "operation": "add",
                        "memory_id": "remote-card-1",
                        "content": "Use 2-opt after nearest-neighbor construction.",
                        "property_name": "good_algorithm",
                        "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                        "entity_id": "entity-1",
                    }
                ]
            },
        }

    updated_statuses: list[dict] = []

    def fake_update_status(_current_user, memory_id: str, *, scope_data: dict, status: str, metadata_patch: dict):
        updated_statuses.append(
            {
                "memory_id": memory_id,
                "scope_data": scope_data,
                "status": status,
                "metadata_patch": metadata_patch,
            }
        )

    monkeypatch.setattr(memory_service, "_mindmemos_stream_post", fake_stream)
    monkeypatch.setattr(memory_service, "_remote_update_card_status", fake_update_status)
    monkeypatch.setattr(memory_service, "_ensure_mindmemos_provider_binding", lambda *_args, **_kwargs: None)

    events = [
        event
        async for event in memory_service.stream_extract_memory_cards(
            db,
            current_user=user,
            scope="user",
            request=MemoryCardExtractionRequest(
                content="Use 2-opt after nearest-neighbor construction.",
                prompt_language="EN",
            ),
        )
    ]

    assert [event["event"] for event in events] == ["progress", "progress", "completed"]
    assert events[0]["stage"] == "llm_extracting"
    assert events[1]["stage"] == "finalizing"
    assert events[1]["percent"] == 96
    assert events[1]["message_i18n"]["zh"] == "正在整理记忆预览"
    assert events[-1]["preview_id"].startswith("llm4ad-preview-")
    assert events[-1]["stage"] == "completed"
    assert events[-1]["percent"] == 100
    assert events[-1]["message_i18n"]["zh"] == "记忆提取完成"
    assert events[-1]["items"][0]["id"] == "remote-card-1"
    assert events[-1]["items"][0]["enabled"] is False
    assert progress_payloads[0]["prompt_language"] == "EN"
    assert updated_statuses[0]["status"] == "archived"


@pytest.mark.asyncio
async def test_stream_extract_memory_cards_reports_empty_completion_message(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)

    async def fake_stream(_current_user, path: str, payload: dict, *, scopes: list[str]):
        assert path == "/v1/memory/add/stream"
        yield {
            "event": "completed",
            "stage": "completed",
            "message": "done",
            "data": {"memories": []},
        }

    monkeypatch.setattr(memory_service, "_mindmemos_stream_post", fake_stream)
    monkeypatch.setattr(memory_service, "_ensure_mindmemos_provider_binding", lambda *_args, **_kwargs: None)

    events = [
        event
        async for event in memory_service.stream_extract_memory_cards(
            db,
            current_user=user,
            scope="user",
            request=MemoryCardExtractionRequest(content="too vague"),
        )
    ]

    assert [event["event"] for event in events] == ["progress", "completed"]
    assert events[0]["stage"] == "finalizing"
    assert events[-1]["items"] == []
    assert events[-1]["message_i18n"]["zh"] == "没有提取到可保存的记忆"
    assert "LLM4AD" in events[-1]["message"]


@pytest.mark.asyncio
async def test_stream_promote_task_memory_cards_uses_structured_sources_and_project_scope(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    source_cards = {
        "task-card-1": MemoryCardResponse(
            id="task-card-1",
            type="good_algorithm",
            title="2-opt refinement",
            content="Apply 2-opt after nearest-neighbor construction.",
            tags=["TSP", "local-search"],
        ),
        "task-card-2": MemoryCardResponse(
            id="task-card-2",
            type="error_reflection",
            title="Large instance limit",
            content="For large instances, restrict the neighborhood size.",
            tags=["TSP"],
        ),
    }
    captured: dict[str, object] = {}

    def fake_fetch(_current_user, scope_data: dict[str, str], memory_ids: list[str]):
        captured["source_scope"] = scope_data
        assert memory_ids == ["task-card-1", "task-card-2"]
        return source_cards

    async def fake_stream(_current_user, path: str, payload: dict, *, scopes: list[str]):
        captured["path"] = path
        captured["payload"] = payload
        captured["scopes"] = scopes
        yield {
            "event": "completed",
            "data": {
                "memories": [
                    {
                        "operation": "add",
                        "memory_id": "project-preview-1",
                        "content": "Use 2-opt with a bounded neighborhood for TSP.",
                        "property_name": "good_algorithm",
                        "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                        "entity_id": "project-entity-1",
                    }
                ]
            },
        }

    archived: list[dict] = []

    def fake_archive(_current_user, memory_id: str, *, scope_data: dict, status: str, metadata_patch: dict):
        archived.append(
            {
                "memory_id": memory_id,
                "scope_data": scope_data,
                "status": status,
                "metadata": metadata_patch,
            }
        )

    monkeypatch.setattr(memory_service, "_remote_fetch_cards_by_ids", fake_fetch)
    monkeypatch.setattr(memory_service, "_mindmemos_stream_post", fake_stream)
    monkeypatch.setattr(memory_service, "_remote_update_card_status", fake_archive)
    monkeypatch.setattr(memory_service, "_ensure_mindmemos_provider_binding", lambda *_args, **_kwargs: None)

    events = [
        event
        async for event in memory_service.stream_promote_task_memory_cards(
            db,
            current_user=user,
            request=TaskMemoryPromotionRequest(
                project_id=task.project_id,
                task_id=task.id,
                memory_ids=["task-card-1", "task-card-2"],
                prompt_language="EN",
            ),
        )
    ]

    payload = captured["payload"]
    assert captured["path"] == "/v1/memory/add/stream"
    assert captured["scopes"] == ["memory:write"]
    assert captured["source_scope"] == {
        "user_id": str(user.id),
        "app_id": "llm4ad",
        "agent_id": "task",
        "session_id": str(task.id),
    }
    assert payload["agent_id"] == "project"
    assert payload["session_id"] == str(task.project_id)
    assert payload["messages"] == [
        {
            "role": "user",
            "content": "User explicitly confirms and requests promotion of the following selected task-memory cards into reusable project memory.",
        },
        {
            "role": "assistant",
            "content": "Selected task memory card (ID: task-card-1)\nTitle: 2-opt refinement\nType: good_algorithm\nTags: TSP, local-search\nContent:\nApply 2-opt after nearest-neighbor construction.",
        },
        {
            "role": "assistant",
            "content": "Selected task memory card (ID: task-card-2)\nTitle: Large instance limit\nType: error_reflection\nTags: TSP\nContent:\nFor large instances, restrict the neighborhood size.",
        },
    ]
    assert payload["metadata"]["llm4ad_source_task_id"] == str(task.id)
    assert payload["metadata"]["llm4ad_source_memory_ids"] == ["task-card-1", "task-card-2"]
    assert "task_id" not in payload
    assert events[-1]["items"][0]["enabled"] is False
    assert archived[0]["scope_data"] == {
        "user_id": str(user.id),
        "app_id": "llm4ad",
        "agent_id": "project",
        "session_id": str(task.project_id),
    }
    assert archived[0]["metadata"]["llm4ad_source_task_id"] == str(task.id)
    assert archived[0]["metadata"]["llm4ad_source_memory_ids"] == ["task-card-1", "task-card-2"]


@pytest.mark.asyncio
async def test_stream_promote_task_memory_cards_rejects_missing_task_scope_card(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    monkeypatch.setattr(memory_service, "_ensure_mindmemos_provider_binding", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(memory_service, "_remote_fetch_cards_by_ids", lambda *_args, **_kwargs: {})

    with pytest.raises(HTTPException) as exc:
        async for _event in memory_service.stream_promote_task_memory_cards(
            db,
            current_user=user,
            request=TaskMemoryPromotionRequest(
                project_id=task.project_id,
                task_id=task.id,
                memory_ids=["missing-card"],
            ),
        ):
            pass

    assert exc.value.status_code == 404
    assert "当前任务范围" in str(exc.value.detail)


def test_remote_list_groups_llm4ad_schema_properties_into_lightweight_cards(monkeypatch: pytest.MonkeyPatch):
    user = models.User(id=uuid.uuid4(), email="schema-card@example.com", hashed_password="x")

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        assert path == "/v1/memory/list"
        return {
            "code": "ok",
            "data": {
                "memories": [
                    {
                        "id": "name-row",
                        "memory": "TSP local search",
                        "status": "active",
                        "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                        "entity_id": "entity-1",
                        "entity_name": "TSP local search",
                        "property_name": "name",
                    },
                    {
                        "id": "content-row",
                        "memory": "Run 2-opt after nearest-neighbor seeding.",
                        "status": "active",
                        "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                        "entity_id": "entity-1",
                        "entity_name": "TSP local search",
                        "property_name": "good_algorithm",
                    },
                    {
                        "id": "tags-row",
                        "memory": "TSP, 2-opt, local-search",
                        "status": "active",
                        "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                        "entity_id": "entity-1",
                        "entity_name": "TSP local search",
                        "property_name": "tags",
                    },
                    {
                        "id": "episode-row",
                        "memory": "Original user input.",
                        "status": "active",
                        "entity_type": "episodes",
                        "property_name": "input_messages",
                    },
                ],
                "page": 1,
                "page_size": 20,
                "total": 4,
                "has_more": False,
            },
        }

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)

    page = memory_service._remote_list_cards(  # noqa: SLF001
        user,
        {"user_id": str(user.id), "app_id": "llm4ad", "agent_id": "global", "session_id": "global"},
        page=1,
        page_size=20,
    )

    assert page.total == 1
    assert page.has_more is False
    assert len(page.items) == 1
    card = page.items[0]
    assert card.id == "content-row"
    assert card.title == "TSP local search"
    assert card.type == "good_algorithm"
    assert card.content == "Run 2-opt after nearest-neighbor seeding."
    assert card.tags == ["TSP", "2-opt", "local-search"]


def test_remote_list_uses_renderable_card_count_when_raw_rows_do_not_form_cards(
    monkeypatch: pytest.MonkeyPatch,
):
    user = models.User(id=uuid.uuid4(), email="schema-empty@example.com", hashed_password="x")

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        assert path == "/v1/memory/list"
        return {
            "code": "ok",
            "data": {
                "memories": [
                    {
                        "id": "tags-only",
                        "memory": "TSP, 2-opt",
                        "status": "active",
                        "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                        "entity_id": "entity-1",
                        "property_name": "tags",
                    }
                ],
                "page": 1,
                "page_size": 20,
                "total": 1,
                "has_more": False,
            },
        }

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)

    page = memory_service._remote_list_cards(  # noqa: SLF001
        user,
        {"user_id": str(user.id), "app_id": "llm4ad", "agent_id": "global", "session_id": "global"},
        page=1,
        page_size=20,
    )

    assert page.items == []
    assert page.total == 0
    assert page.has_more is False


def test_remote_list_merges_schema_tags_by_entity_name_metadata(monkeypatch: pytest.MonkeyPatch):
    user = models.User(id=uuid.uuid4(), email="entity-name-tags@example.com", hashed_password="x")
    calls: list[dict] = []

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        calls.append(payload)
        assert path == "/v1/memory/list"
        if payload.get("filters", {}).get("property_name") == "tags":
            return {
                "code": "ok",
                "data": {
                    "memories": [
                        {
                            "id": "tags-1",
                            "memory": "mutation rate, population collapse, algorithm design",
                            "status": "active",
                            "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                            "entity_id": "entity-1",
                            "property_name": "tags",
                            "metadata": {
                                "entity_name": "LLM4AD Algorithm Memory Card",
                            },
                        },
                    ],
                    "page": 1,
                    "page_size": 20,
                    "total": 1,
                    "has_more": False,
                },
            }
        return {
            "code": "ok",
            "data": {
                "memories": [
                    {
                        "id": "card-1",
                        "memory": "Large mutation rates can collapse the population.",
                        "status": "active",
                        "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                        "entity_id": "entity-1",
                        "property_name": "error_reflection",
                        "metadata": {
                            "entity_name": "LLM4AD Algorithm Memory Card",
                        },
                    },
                ],
                "page": 1,
                "page_size": 20,
                "total": 1,
                "has_more": False,
            },
        }

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)

    page = memory_service._remote_list_cards(  # noqa: SLF001
        user,
        {"user_id": str(user.id), "app_id": "llm4ad", "agent_id": "global", "session_id": "global"},
        page=1,
        page_size=20,
    )

    assert [card.id for card in page.items] == ["card-1"]
    assert page.items[0].tags == ["mutation rate", "population collapse", "algorithm design"]
    assert calls[0]["filters"] == {
        "user_id": str(user.id),
        "app_id": "llm4ad",
        "session_id": "global",
        "agent_id": "global",
        "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
        "property_name": {
            "in": ["good_algorithm", "error_reflection", "domain_knowledge", "general_insight"]
        },
    }
    assert calls[1]["filters"] == {
        "user_id": str(user.id),
        "app_id": "llm4ad",
        "session_id": "global",
        "agent_id": "global",
        "entity_id": {"in": ["entity-1"]},
        "property_name": "tags",
    }


def test_remote_list_cards_by_scope_pagination_with_tags_metadata(monkeypatch: pytest.MonkeyPatch):
    user = models.User(id=uuid.uuid4(), email="tag-filter@example.com", hashed_password="x")
    calls: list[tuple[str, dict]] = []

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        calls.append((path, payload))
        assert path == "/v1/memory/list"
        if payload.get("filters", {}).get("property_name") == "tags":
            return {
                "code": "ok",
                "data": {
                    "memories": [
                        {
                            "id": "tags-1",
                            "memory": "TSP, 2-opt",
                            "property_name": "tags",
                            "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                            "entity_id": "entity-1",
                        },
                    ],
                    "page": 1,
                    "page_size": 20,
                    "total": 1,
                    "has_more": False,
                },
            }
        return {
            "code": "ok",
            "data": {
                "memories": [
                    {
                        "id": "card-1",
                        "memory": "Run 2-opt.",
                        "property_name": "good_algorithm",
                        "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                        "entity_id": "entity-1",
                    },
                    {
                        "id": "card-2",
                        "memory": "Use annealing.",
                        "property_name": "domain_knowledge",
                        "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                        "entity_id": "entity-2",
                    },
                ],
                "page": 1,
                "page_size": 20,
                "total": 2,
                "has_more": False,
            },
        }

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)

    page = memory_service._remote_list_cards(  # noqa: SLF001
        user,
        {"user_id": str(user.id), "app_id": "llm4ad", "agent_id": "global", "session_id": "global"},
        page=1,
        page_size=20,
    )

    assert [card.id for card in page.items] == ["card-1", "card-2"]
    assert page.total == 2
    assert page.items[0].tags == ["TSP", "2-opt"]
    assert page.items[1].tags == []
    assert [payload.get("filters") for _path, payload in calls] == [
        {
            "user_id": str(user.id),
            "app_id": "llm4ad",
            "session_id": "global",
            "agent_id": "global",
            "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
            "property_name": {
                "in": ["good_algorithm", "error_reflection", "domain_knowledge", "general_insight"]
            },
        },
        {
            "user_id": str(user.id),
            "app_id": "llm4ad",
            "session_id": "global",
            "agent_id": "global",
            "entity_id": {"in": ["entity-1", "entity-2"]},
            "property_name": "tags",
        },
    ]


def test_remote_list_cards_uses_remote_total_when_available(monkeypatch: pytest.MonkeyPatch):
    user = models.User(id=uuid.uuid4(), email="page-total@example.com", hashed_password="x")

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        assert path == "/v1/memory/list"
        if payload.get("filters", {}).get("property_name") == "tags":
            return {
                "code": "ok",
                "data": {
                    "memories": [],
                    "page": 1,
                    "page_size": 20,
                    "total": 0,
                    "has_more": False,
                },
            }
        return {
            "code": "ok",
            "data": {
                "memories": [
                    {
                        "id": "card-1",
                        "memory": "Run 2-opt.",
                        "property_name": "good_algorithm",
                        "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                        "entity_id": "entity-1",
                    },
                    {
                        "id": "card-2",
                        "memory": "Use annealing.",
                        "property_name": "domain_knowledge",
                        "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                        "entity_id": "entity-2",
                    },
                ],
                "page": 2,
                "page_size": 2,
                "total": 37,
                "has_more": True,
            },
        }

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)

    page = memory_service._remote_list_cards(  # noqa: SLF001
        user,
        {"user_id": str(user.id), "app_id": "llm4ad", "agent_id": "global", "session_id": "global"},
        page=2,
        page_size=2,
    )

    assert len(page.items) == 2
    assert page.page == 2
    assert page.page_size == 2
    assert page.total == 37
    assert page.has_more is True


def test_remote_list_cards_merges_entity_name_tags_metadata(monkeypatch: pytest.MonkeyPatch):
    user = models.User(id=uuid.uuid4(), email="tag-filter-entity-name@example.com", hashed_password="x")
    calls: list[dict] = []

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        calls.append(payload)
        assert path == "/v1/memory/list"
        if payload.get("filters", {}).get("property_name") == "tags":
            return {
                "code": "ok",
                "data": {
                    "memories": [
                        {
                            "id": "tags-1",
                            "memory": "mutation rate, population collapse",
                            "property_name": "tags",
                            "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                            "entity_id": "entity-1",
                            "metadata": {
                                "entity_name": "LLM4AD Algorithm Memory Card",
                            },
                        },
                    ],
                    "page": 1,
                    "page_size": 20,
                    "total": 1,
                    "has_more": False,
                },
            }
        return {
            "code": "ok",
            "data": {
                "memories": [
                    {
                        "id": "card-1",
                        "memory": "Large mutation rates can collapse the population.",
                        "property_name": "error_reflection",
                        "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                        "entity_id": "entity-1",
                        "metadata": {
                            "entity_name": "LLM4AD Algorithm Memory Card",
                        },
                    },
                ],
                "page": 1,
                "page_size": 20,
                "total": 1,
                "has_more": False,
            },
        }

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)

    page = memory_service._remote_list_cards(  # noqa: SLF001
        user,
        {"user_id": str(user.id), "app_id": "llm4ad", "agent_id": "global", "session_id": "global"},
        page=1,
        page_size=20,
    )

    assert [card.id for card in page.items] == ["card-1"]
    assert page.items[0].tags == ["mutation rate", "population collapse"]
    assert [payload.get("filters") for payload in calls] == [
        {
            "user_id": str(user.id),
            "app_id": "llm4ad",
            "session_id": "global",
            "agent_id": "global",
            "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
            "property_name": {
                "in": ["good_algorithm", "error_reflection", "domain_knowledge", "general_insight"]
            },
        },
        {
            "user_id": str(user.id),
            "app_id": "llm4ad",
            "session_id": "global",
            "agent_id": "global",
            "entity_id": {"in": ["entity-1"]},
            "property_name": "tags",
        },
    ]


def test_remote_items_to_cards_includes_archived_generated_memories_by_default():
    cards = memory_service._remote_items_to_cards(  # noqa: SLF001
        [
            {
                "id": "generated-disabled",
                "memory": "Generated memory content.",
                "status": "archived",
                "property_name": "good_algorithm",
                "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                "entity_id": "generated-disabled-entity",
                "metadata": {
                    "llm4ad_generation_id": "generation-test",
                    "memory_type": "good_algorithm",
                },
            },
            {
                "id": "archived-card",
                "memory": "A deliberately archived but non-preview memory.",
                "status": "archived",
                "property_name": "general_insight",
                "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                "entity_id": "archived-card-entity",
                "metadata": {
                    "memory_type": "general_insight",
                    "title": "Archived card",
                },
            },
        ]
    )

    assert [card.id for card in cards] == ["generated-disabled", "archived-card"]
    assert cards[0].enabled is False


def test_remote_items_to_cards_ignores_fact_items_without_schema_property_projection():
    cards = memory_service._remote_items_to_cards(  # noqa: SLF001
        [
            {
                "id": "generated-fact",
                "memory": "当前项目更重视算法稳定性、可解释性和可复现实验结果。",
                "memory_type": "fact",
                "status": "active",
                "metadata": {
                    "entity_name": "项目",
                    "request_metadata": {
                        "record_metadata": [
                            {
                                "source": "llm4ad",
                                "llm4ad_generation_id": "generation-test",
                                "enabled": False,
                            }
                        ]
                    },
                },
            },
            {
                "id": "generated-episode",
                "memory": "对话时间戳: 2026-07-10 ...",
                "memory_type": "episodic",
                "status": "active",
                "metadata": {
                    "entity_name": "项目算法稳定性与可解释性的重要性",
                    "request_metadata": {
                        "record_metadata": [
                            {
                                "source": "llm4ad",
                                "llm4ad_generation_id": "generation-test",
                                "enabled": False,
                            }
                        ]
                    },
                },
            },
        ]
    )

    assert cards == []


def test_task_memory_update_does_not_echo_readonly_metadata(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    _store, calls = _fake_mindmemos(monkeypatch, {"remote-existing": "Existing MindMemOS content."})

    memory_service.upsert_task_memory_card(
        db,
        task.id,
        user,
        MemoryCardUpsertRequest(
            id="remote-existing",
            type="general_insight",
            title="Editable title",
            content="Editable content.",
            enabled=True,
            tags=["editable"],
            metadata={
                "property_time": "2026-07-09",
                "entity_name": "Readonly entity",
                "content_hash": "readonly-hash",
            },
        ),
    )

    del _store
    update_payload = [payload for path, payload in calls if path == "/v1/memory/update"][-1]
    assert update_payload["metadata_patch"] == {
        "source": "llm4ad",
        "memory_type": "general_insight",
        "title": "Editable title",
        "enabled": True,
        "tags": ["editable"],
    }


def test_task_memory_update_returns_refetched_remote_card(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    _store, _calls = _fake_mindmemos(monkeypatch, {"remote-existing": "Existing content."})
    fetch_calls: list[list[str]] = []

    def fake_fetch(_current_user, _scope_data, memory_ids: list[str]):
        fetch_calls.append(memory_ids)
        return {
            "remote-existing": MemoryCardResponse(
                id="remote-existing",
                type="good_algorithm",
                title="Remote normalized title",
                content="Remote normalized content.",
                enabled=False,
                source="mindmemos",
                tags=["remote"],
            )
        }

    monkeypatch.setattr(memory_service, "_remote_fetch_cards_by_ids", fake_fetch)

    card = memory_service.upsert_task_memory_card(
        db,
        task.id,
        user,
        MemoryCardUpsertRequest(
            id="remote-existing",
            type="general_insight",
            title="Editable title",
            content="Editable content.",
            enabled=False,
            tags=["editable"],
        ),
    )

    del _store
    assert fetch_calls == [["remote-existing"]]
    assert card.title == "Remote normalized title"
    assert card.content == "Remote normalized content."
    assert card.enabled is False
    assert card.tags == ["remote"]


def test_task_memory_update_falls_back_when_refetch_misses(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    _store, _calls = _fake_mindmemos(monkeypatch, {"remote-existing": "Existing content."})
    fetch_calls: list[list[str]] = []

    def fake_fetch(_current_user, _scope_data, memory_ids: list[str]):
        fetch_calls.append(memory_ids)
        return {}

    monkeypatch.setattr(memory_service, "_remote_fetch_cards_by_ids", fake_fetch)

    card = memory_service.upsert_task_memory_card(
        db,
        task.id,
        user,
        MemoryCardUpsertRequest(
            id="remote-existing",
            type="general_insight",
            title="Editable title",
            content="Editable content.",
            enabled=True,
            tags=["editable"],
        ),
    )

    del _store
    assert fetch_calls == [["remote-existing"]]
    assert card.title == "Editable title"
    assert card.content == "Editable content."
    assert card.enabled is True
    assert card.tags == ["editable"]


def test_memory_card_extraction_archives_new_memories_as_visible_disabled_cards(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    _store, calls = _fake_mindmemos(monkeypatch)

    preview = memory_service.extract_memory_cards(
        db,
        current_user=user,
        scope="task",
        task_id=task.id,
        request=MemoryCardExtractionRequest(
            content="在 TSP 中，2-opt 对中小规模实例稳定，但大规模实例需要限制邻域数量。"
        ),
    )

    assert preview.preview_id
    assert [item.id for item in preview.items] == ["remote-card-1"]
    assert preview.items[0].enabled is False
    assert preview.items[0].type == "good_algorithm"
    assert preview.items[0].content == "在 TSP 中，2-opt 对中小规模实例稳定，但大规模实例需要限制邻域数量。"

    add_calls = [payload for path, payload in calls if path == "/v1/memory/add"]
    update_calls = [payload for path, payload in calls if path == "/v1/memory/update"]
    assert len(add_calls) == 1
    assert add_calls[0]["messages"] == [
        {"role": "user", "content": "在 TSP 中，2-opt 对中小规模实例稳定，但大规模实例需要限制邻域数量。"}
    ]
    assert "prompt_language" not in add_calls[0]
    assert update_calls[-1]["memory_id"] == "remote-card-1"
    assert update_calls[-1]["status"] == "archived"
    assert "content" not in update_calls[-1]
    assert "llm4ad_preview_pending" not in update_calls[-1]["metadata_patch"]
    assert update_calls[-1]["metadata_patch"]["enabled"] is False
    assert update_calls[-1]["metadata_patch"]["memory_type"] == "good_algorithm"

    listed = memory_service.list_task_memory_cards(db, task.id, user)
    assert [item.id for item in listed.items] == ["remote-card-1"]
    assert listed.items[0].enabled is False


def test_memory_card_extraction_passes_prompt_language_to_mindmemos(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    _store, calls = _fake_mindmemos(monkeypatch)

    memory_service.extract_memory_cards(
        db,
        current_user=user,
        scope="task",
        task_id=task.id,
        request=MemoryCardExtractionRequest(
            content="Prefer stable and interpretable algorithms for this project.",
            prompt_language="EN",
        ),
    )

    add_payload = [payload for path, payload in calls if path == "/v1/memory/add"][-1]
    assert add_payload["prompt_language"] == "EN"
    assert add_payload["metadata"]["llm4ad_prompt_language"] == "EN"


def test_memory_card_extraction_uses_related_memory_ids_from_schema_events(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    calls: list[tuple[str, dict]] = []

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        calls.append((path, payload))
        if path == "/v1/memory/add":
            return {
                "code": "ok",
                "data": {
                    "memories": [
                        {
                            "operation": "add",
                            "memory_id": "entity-1",
                            "content": "Entity: TSP 2-opt (Type: llm4ad_memory_card)",
                            "memory_type": "fact",
                            "related_memory_ids": ["prop-good", "prop-error"],
                        }
                    ]
                },
            }
        if path == "/v1/memory/list":
            _assert_task_scope_filters(payload["filters"], user, task, ["prop-good", "prop-error"])
            return {
                "code": "ok",
                "data": {
                    "memories": [
                        {
                            "id": "prop-good",
                            "memory": "Use 2-opt after nearest-neighbor seeding.",
                            "status": "active",
                            "mem_type": "fact",
                            "property_name": "good_algorithm",
                            "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                            "entity_id": "entity-1",
                            "metadata": {"property_name": "good_algorithm"},
                        },
                        {
                            "id": "prop-error",
                            "memory": "Avoid very high mutation rates on small populations.",
                            "status": "active",
                            "mem_type": "fact",
                            "property_name": "error_reflection",
                            "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                            "entity_id": "entity-2",
                            "metadata": {"property_name": "error_reflection"},
                        },
                    ],
                    "page": 1,
                    "page_size": 2,
                    "total": 2,
                    "has_more": False,
                },
            }
        if path == "/v1/memory/update":
            return {"code": "ok", "data": None}
        if path == "/v1/memory/delete":
            return {"code": "ok", "data": None}
        raise AssertionError(f"unexpected MindMemOS path: {path}")

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)
    monkeypatch.setattr(memory_service, "_ensure_mindmemos_provider_binding", lambda db, current_user: None)

    preview = memory_service.extract_memory_cards(
        db,
        current_user=user,
        scope="task",
        task_id=task.id,
        request=MemoryCardExtractionRequest(content="2-opt works, high mutation failed."),
    )

    assert [item.id for item in preview.items] == ["prop-good", "prop-error"]
    assert [item.type for item in preview.items] == ["good_algorithm", "error_reflection"]
    update_ids = [payload["memory_id"] for path, payload in calls if path == "/v1/memory/update"]
    assert update_ids == ["prop-good", "prop-error"]
    assert "entity-1" not in update_ids


def test_memory_card_extraction_merges_related_schema_tags(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        if path == "/v1/memory/add":
            return {
                "code": "ok",
                "data": {
                    "memories": [
                        {
                            "operation": "add",
                            "memory_id": "entity-1",
                            "content": "Entity: TSP 2-opt (Type: llm4ad_memory_card)",
                            "memory_type": "fact",
                            "related_memory_ids": ["prop-good", "tags-1"],
                        }
                    ]
                },
            }
        if path == "/v1/memory/list":
            filters = payload["filters"]
            if filters.get("memory_id") == {"in": ["prop-good", "tags-1"]}:
                _assert_task_scope_filters(filters, user, task, ["prop-good", "tags-1"])
                return {
                    "code": "ok",
                    "data": {
                        "memories": [
                            {
                                "id": "prop-good",
                                "memory": "Use 2-opt after nearest-neighbor seeding.",
                                "status": "active",
                                "mem_type": "fact",
                                "property_name": "good_algorithm",
                                "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                                "entity_id": "entity-1",
                                "metadata": {"property_name": "good_algorithm"},
                            },
                        {
                            "id": "tags-1",
                            "memory": "TSP, 2-opt, local-search",
                            "status": "active",
                            "property_name": "tags",
                            "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                            "entity_id": "entity-1",
                            "metadata": {"property_name": "tags"},
                        }
                    ],
                    "page": 1,
                    "page_size": 2,
                    "total": 2,
                    "has_more": False,
                },
            }
        if path == "/v1/memory/update":
            return {"code": "ok", "data": None}
        raise AssertionError(f"unexpected MindMemOS path: {path}")

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)
    monkeypatch.setattr(memory_service, "_ensure_mindmemos_provider_binding", lambda db, current_user: None)

    preview = memory_service.extract_memory_cards(
        db,
        current_user=user,
        scope="task",
        task_id=task.id,
        request=MemoryCardExtractionRequest(content="2-opt works for TSP."),
    )

    assert [item.id for item in preview.items] == ["prop-good"]
    assert preview.items[0].tags == ["TSP", "2-opt", "local-search"]


def test_memory_card_extraction_merges_related_schema_tags_by_entity_name(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        if path == "/v1/memory/add":
            return {
                "code": "ok",
                "data": {
                    "memories": [
                        {
                            "operation": "add",
                            "memory_id": "entity-1",
                            "content": "Entity: LLM4AD Algorithm Memory Card",
                            "memory_type": "fact",
                            "related_memory_ids": ["prop-good", "tags-1"],
                        }
                    ]
                },
            }
        if path == "/v1/memory/list":
            filters = payload["filters"]
            if filters.get("memory_id") == {"in": ["prop-good", "tags-1"]}:
                _assert_task_scope_filters(filters, user, task, ["prop-good", "tags-1"])
                return {
                    "code": "ok",
                    "data": {
                        "memories": [
                            {
                                "id": "prop-good",
                                "memory": "Large mutation rates can collapse the population.",
                                "status": "active",
                                "mem_type": "fact",
                                "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                                "entity_id": "entity-1",
                                "metadata": {
                                    "entity_name": "LLM4AD Algorithm Memory Card",
                                    "memory_type": "error_reflection",
                                },
                            },
                            {
                                "id": "tags-1",
                                "memory": "mutation rate, population collapse",
                                "status": "active",
                                "property_name": "tags",
                                "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                                "entity_id": "entity-1",
                                "metadata": {
                                    "entity_name": "LLM4AD Algorithm Memory Card",
                                },
                            }
                        ],
                        "page": 1,
                        "page_size": 2,
                        "total": 2,
                        "has_more": False,
                    },
                }
            return {"code": "ok", "data": {"memories": [], "page": 1, "page_size": 20, "total": 0, "has_more": False}}
        if path == "/v1/memory/update":
            return {"code": "ok", "data": None}
        raise AssertionError(f"unexpected MindMemOS path: {path}")

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)
    monkeypatch.setattr(memory_service, "_ensure_mindmemos_provider_binding", lambda db, current_user: None)

    preview = memory_service.extract_memory_cards(
        db,
        current_user=user,
        scope="task",
        task_id=task.id,
        request=MemoryCardExtractionRequest(content="Large mutation caused collapse."),
    )

    assert [item.id for item in preview.items] == ["prop-good"]
    assert preview.items[0].tags == ["mutation rate", "population collapse"]


def test_memory_card_extraction_keeps_502_when_add_times_out(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    calls: list[tuple[str, dict]] = []

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        calls.append((path, payload))
        if path == "/v1/memory/add":
            raise HTTPException(status_code=502, detail="MindMemOS request failed: timed out")
        raise AssertionError(f"unexpected MindMemOS path: {path}")

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)
    monkeypatch.setattr(memory_service, "_ensure_mindmemos_provider_binding", lambda db, current_user: None)

    with pytest.raises(HTTPException) as exc:
        memory_service.extract_memory_cards(
            db,
            current_user=user,
            scope="task",
            task_id=task.id,
            request=MemoryCardExtractionRequest(content="This add call times out."),
        )

    assert exc.value.status_code == 502
    assert "timed out" in str(exc.value.detail)
    assert [path for path, _payload in calls] == ["/v1/memory/add"]


def test_memory_card_extraction_keeps_502_when_timeout_recovery_misses(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        if path == "/v1/memory/add":
            raise HTTPException(status_code=502, detail="MindMemOS request failed: timed out")
        if path == "/v1/memory/list":
            return {
                "code": "ok",
                "data": {
                    "memories": [],
                    "page": payload.get("page", 1),
                    "page_size": payload.get("page_size", 20),
                    "total": 0,
                    "has_more": False,
                },
            }
        raise AssertionError(f"unexpected MindMemOS path: {path}")

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)
    monkeypatch.setattr(memory_service, "_ensure_mindmemos_provider_binding", lambda db, current_user: None)

    with pytest.raises(HTTPException) as exc:
        memory_service.extract_memory_cards(
            db,
            current_user=user,
            scope="task",
            task_id=task.id,
            request=MemoryCardExtractionRequest(content="This add call times out before writing."),
        )

    assert exc.value.status_code == 502
    assert "timed out" in str(exc.value.detail)


def test_memory_card_extraction_commit_activates_selected_and_keeps_rejected_disabled(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    _store, calls = _fake_mindmemos(
        monkeypatch,
        {
            "keep-card": "Keep this extracted memory.",
            "drop-card": "Drop this extracted memory.",
        },
        {
            "keep-card": {
                "llm4ad_generation_id": "preview-test",
                "memory_type": "general_insight",
            },
            "drop-card": {
                "llm4ad_generation_id": "preview-test",
                "memory_type": "general_insight",
            },
        },
    )

    result = memory_service.commit_memory_card_extraction(
        db,
        current_user=user,
        scope="task",
        task_id=task.id,
        preview_id="preview-test",
        request=MemoryCardExtractionCommitRequest(
            selected_ids=["keep-card"],
            all_ids=["keep-card", "drop-card"],
        ),
    )

    assert [item.id for item in result.items] == ["keep-card"]
    assert result.items[0].enabled is True
    assert "drop-card" in _store
    assert [path for path, _payload in calls].count("/v1/memory/add") == 0
    update_calls = [payload for path, payload in calls if path == "/v1/memory/update"]
    delete_calls = [payload for path, payload in calls if path == "/v1/memory/delete"]
    assert update_calls[-1]["memory_id"] == "keep-card"
    assert update_calls[-1]["status"] == "active"
    assert "content" not in update_calls[-1]
    assert "llm4ad_preview_pending" not in update_calls[-1]["metadata_patch"]
    assert delete_calls == []


def test_memory_card_extraction_commit_can_activate_disabled_generated_cards(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    calls: list[tuple[str, dict]] = []
    preview_id = "generation-test"

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        calls.append((path, payload))
        if path == "/v1/memory/list":
            _assert_task_scope_filters(payload["filters"], user, task, ["keep-card"])
            return {
                "code": "ok",
                "data": {
                    "memories": [
                        {
                            "id": "keep-card",
                            "memory": "Keep this extracted memory.",
                            "status": "archived",
                            "property_name": "good_algorithm",
                            "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                            "entity_id": "keep-entity",
                            "metadata": {
                                "memory_type": "good_algorithm",
                                "title": "Generated keep",
                                "llm4ad_generation_id": preview_id,
                            },
                        },
                    ],
                    "page": 1,
                    "page_size": 1,
                    "total": 1,
                    "has_more": False,
                },
            }
        if path == "/v1/memory/update":
            return {"code": "ok", "data": None}
        if path == "/v1/memory/delete":
            return {"code": "ok", "data": None}
        raise AssertionError(f"unexpected MindMemOS path: {path}")

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)
    monkeypatch.setattr(memory_service, "_ensure_mindmemos_provider_binding", lambda db, current_user: None)

    result = memory_service.commit_memory_card_extraction(
        db,
        current_user=user,
        scope="task",
        task_id=task.id,
        preview_id=preview_id,
        request=MemoryCardExtractionCommitRequest(
            selected_ids=["keep-card"],
            all_ids=["keep-card", "drop-card"],
        ),
    )

    assert [item.id for item in result.items] == ["keep-card"]
    assert result.items[0].enabled is True
    update_calls = [payload for path, payload in calls if path == "/v1/memory/update"]
    delete_calls = [payload for path, payload in calls if path == "/v1/memory/delete"]
    assert update_calls[-1]["memory_id"] == "keep-card"
    assert "llm4ad_preview_pending" not in update_calls[-1]["metadata_patch"]
    assert delete_calls == []


def test_memory_card_extraction_commit_activates_scope_cards_without_preview_gate(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    calls: list[tuple[str, dict]] = []

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        calls.append((path, payload))
        if path == "/v1/memory/list":
            return {
                "code": "ok",
                "data": {
                    "memories": [
                        {
                            "id": "other-preview-card",
                            "memory": "This belongs to another preview.",
                            "status": "archived",
                            "property_name": "good_algorithm",
                            "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                            "entity_id": "other-preview-entity",
                            "metadata": {
                                "memory_type": "good_algorithm",
                                "llm4ad_generation_id": "other-generation",
                            },
                        }
                    ],
                    "page": 1,
                    "page_size": 1,
                    "total": 1,
                    "has_more": False,
                },
            }
        if path == "/v1/memory/update":
            return {"code": "ok", "data": None}
        raise AssertionError(f"unexpected MindMemOS path: {path}")

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)
    monkeypatch.setattr(memory_service, "_ensure_mindmemos_provider_binding", lambda db, current_user: None)

    result = memory_service.commit_memory_card_extraction(
        db,
        current_user=user,
        scope="task",
        task_id=task.id,
        preview_id="current-generation",
        request=MemoryCardExtractionCommitRequest(
            selected_ids=["other-preview-card"],
            all_ids=["other-preview-card"],
        ),
    )

    assert [item.id for item in result.items] == ["other-preview-card"]
    assert result.items[0].enabled is True
    assert [path for path, _payload in calls] == ["/v1/memory/list", "/v1/memory/update"]


def test_memory_card_extraction_discard_hard_deletes_preview_memories(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    _store, calls = _fake_mindmemos(
        monkeypatch,
        {
            "preview-card-a": "Temporary memory A.",
            "preview-card-b": "Temporary memory B.",
        },
    )

    memory_service.discard_memory_card_extraction(
        db,
        current_user=user,
        scope="task",
        task_id=task.id,
        preview_id="preview-test",
        memory_ids=["preview-card-a", "preview-card-b"],
    )

    assert _store == {}
    assert [payload for path, payload in calls if path == "/v1/memory/delete"] == [
        {"memory_id": "preview-card-a", "hard": True},
        {"memory_id": "preview-card-b", "hard": True},
    ]


def test_task_memory_crud_checks_task_authorization(db: Session, monkeypatch: pytest.MonkeyPatch):
    owner = create_random_user(db)
    outsider = create_random_user(db)
    task = _create_task_for_user(db, owner.id)
    _enable_system_mindmemos(monkeypatch)
    _fake_mindmemos(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        memory_service.list_task_memory_cards(db, task.id, outsider)

    assert exc.value.status_code == 403


def test_task_memory_management_is_disabled_without_mindmemos(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_ENABLED", False)

    with pytest.raises(HTTPException) as exc:
        memory_service.list_task_memory_cards(db, task.id, user)

    assert exc.value.status_code == 404
    assert "MindMemOS" in exc.value.detail
