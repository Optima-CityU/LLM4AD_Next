"""Tests for user/project memory defaults and scoped memory cards."""

import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app import models
from app.core.db import engine
from app.schemas import task as task_schemas
from app.schemas.memory import MemoryCardUpsertRequest, MemoryConfigUpdate
from app.services import memory_service, task_service
from app.services.task_service import crud as task_crud
from tests.utils.user import create_random_user


@pytest.fixture(scope="module")
def db():
    with Session(engine) as session:
        yield session


def _create_project(db: Session, user_id: uuid.UUID) -> models.Project:
    project = models.Project(name="Scoped Memory Project", description="", user_id=user_id)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def _fake_mindmemos(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, dict]] = []
    store: dict[str, dict] = {}

    def matches_filter(record: dict, memory_id: str, filters: dict) -> bool:
        requested_ids = set(filters.get("memory_id", {}).get("in", store.keys()))
        if memory_id not in requested_ids:
            return False
        for field in ("user_id", "app_id", "session_id", "agent_id"):
            if filters.get(field) is not None and record["scope"][field] != filters[field]:
                return False
        return True

    def fake_post(_current_user, path: str, payload: dict, *, _scopes: list[str]):
        calls.append((path, payload))
        if path == "/v1/memory/list":
            filters = payload.get("filters") or {}
            memories = [
                {
                    "id": memory_id,
                    "memory": record["content"],
                    "status": record["status"],
                    "mem_type": "fact",
                    "property_name": record["metadata"].get("memory_type", "general_insight"),
                    "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                    "entity_id": memory_id,
                    "metadata": {
                        "entity_type": memory_service.LLM4AD_MEMORY_ENTITY_TYPE,
                        "entity_id": memory_id,
                        **record["metadata"],
                    },
                }
                for memory_id, record in store.items()
                if matches_filter(record, memory_id, filters)
            ]
            return {
                "code": "ok",
                "data": {
                    "memories": memories,
                    "page": payload.get("page", 1),
                    "page_size": payload.get("page_size", 20),
                    "total": len(memories),
                    "has_more": False,
                },
            }
        if path == "/v1/memory/add":
            memory_id = f"remote-{len(store) + 1}"
            store[memory_id] = {
                "scope": {
                    "user_id": payload["user_id"],
                    "app_id": payload["app_id"],
                    "session_id": payload["session_id"],
                    "agent_id": payload["agent_id"],
                },
                "content": payload["messages"][0]["content"],
                "status": "active",
                "metadata": dict(payload.get("metadata") or {}),
            }
            return {"code": "ok", "data": {"memories": [{"memory_id": memory_id}]}}
        if path == "/v1/memory/update":
            record = store[payload["memory_id"]]
            if "content" in payload:
                record["content"] = payload["content"]
            record["status"] = payload.get("status", record["status"])
            record["metadata"].update(payload.get("metadata_patch") or {})
            return {"code": "ok", "data": None}
        if path == "/v1/memory/delete":
            assert payload == {"memory_id": payload["memory_id"]}
            store.pop(payload["memory_id"], None)
            return {"code": "ok", "data": None}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(memory_service, "_mindmemos_post", fake_post)
    monkeypatch.setattr(memory_service, "_ensure_mindmemos_provider_binding", lambda db, current_user: None)
    return calls, store


def _enable_system_mindmemos(monkeypatch: pytest.MonkeyPatch):
    for settings_obj in (memory_service.settings, task_crud.settings):
        monkeypatch.setattr(settings_obj, "LLM4AD_MINDMEMOS_ENABLED", True)
        monkeypatch.setattr(settings_obj, "LLM4AD_MINDMEMOS_BASE_URL", "http://mindmemos-api:8000")
        monkeypatch.setattr(settings_obj, "LLM4AD_MINDMEMOS_JWT_SECRET", "jwt-test-secret")


def _mark_user_memory_bound(db: Session, user_id: uuid.UUID) -> None:
    config = db.exec(
        select(models.UserMemoryConfig).where(models.UserMemoryConfig.user_id == user_id)
    ).first()
    if config is None:
        config = models.UserMemoryConfig(user_id=user_id)
    config.mindmemos_binding_id = "pb_test"
    db.add(config)
    db.commit()


def _assert_scope_payload(payload: dict, user: models.User, *, session_id: str, agent_id: str) -> None:
    assert payload["user_id"] == str(user.id)
    assert payload["app_id"] == memory_service.settings.LLM4AD_MINDMEMOS_APP_ID
    assert payload["session_id"] == session_id
    assert payload["agent_id"] == agent_id


def _assert_scope_filter(filters: dict, user: models.User, *, session_id: str, agent_id: str) -> None:
    assert filters["user_id"] == str(user.id)
    assert filters["app_id"] == memory_service.settings.LLM4AD_MINDMEMOS_APP_ID
    assert filters["session_id"] == session_id
    assert filters["agent_id"] == agent_id
    assert "llm4ad_scope" not in filters
    assert "project_id" not in filters
    assert "task_id" not in filters


class _FakeHttpClient:
    def __init__(self, responses: list[httpx.Response]):
        self._responses = responses
        self.requests: list[tuple[str, str, dict | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get(self, url: str):
        self.requests.append(("GET", url, None))
        return self._responses.pop(0)

    def post(self, url: str, *, headers: dict | None = None, json: dict | None = None):
        self.requests.append(("POST", url, {"headers": headers or {}, "json": json or {}}))
        return self._responses.pop(0)


def _response(status_code: int, payload: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload or {},
        request=httpx.Request("GET", "http://mindmemos-api:8000/test"),
    )


def test_mindmemos_post_uses_long_timeout_for_add_requests(monkeypatch: pytest.MonkeyPatch):
    timeouts: list[float] = []

    class FakeClient:
        def __init__(self, *, timeout):
            timeouts.append(timeout)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url: str, *, headers: dict | None = None, json: dict | None = None):
            return _response(200, {"code": "ok", "data": {}})

    monkeypatch.setattr(memory_service.httpx, "Client", FakeClient)
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_REQUEST_TIMEOUT", 11.0)
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_ADD_TIMEOUT", 123.0)
    user = SimpleNamespace(id=uuid.uuid4())

    memory_service._mindmemos_post(user, "/v1/memory/list", {}, scopes=["memory:read"])
    memory_service._mindmemos_post(user, "/v1/memory/add", {}, scopes=["memory:write"])

    assert timeouts == [11.0, 123.0]


def test_mindmemos_post_treats_zero_timeout_as_infinite(monkeypatch: pytest.MonkeyPatch):
    timeouts: list[float | None] = []

    class FakeClient:
        def __init__(self, *, timeout):
            timeouts.append(timeout)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url: str, *, headers: dict | None = None, json: dict | None = None):
            return _response(200, {"code": "ok", "data": {}})

    monkeypatch.setattr(memory_service.httpx, "Client", FakeClient)
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_REQUEST_TIMEOUT", 0.0)
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_ADD_TIMEOUT", 0.0)
    user = SimpleNamespace(id=uuid.uuid4())

    memory_service._mindmemos_post(user, "/v1/memory/list", {}, scopes=["memory:read"])
    memory_service._mindmemos_post(user, "/v1/memory/add", {}, scopes=["memory:write"])

    assert timeouts == [None, None]


def test_user_memory_config_defaults_and_updates(db: Session):
    user = create_random_user(db)

    config = memory_service.get_user_memory_config(db, user)
    assert config.enabled is True
    assert config.include_user_memory is False
    assert config.include_project_memory is False
    assert config.include_task_memory is True
    assert config.user_memory_limit == 0
    assert config.project_memory_limit == 0
    assert config.task_memory_limit == 5
    assert config.mindmemos_score_threshold is None

    updated = memory_service.update_user_memory_config(
        db,
        user,
        MemoryConfigUpdate(
            enabled=False,
            include_project_memory=False,
            project_memory_limit=2,
            mindmemos_search_strategy="agentic",
        ),
    )

    assert updated.enabled is False
    assert updated.include_project_memory is False
    assert updated.project_memory_limit == 2
    assert updated.mindmemos_search_strategy == "agentic"


def test_user_memory_config_enables_rerank_by_default_when_system_rerank_is_configured(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    monkeypatch.setattr(memory_service.settings, "MINDMEMOS_RERANK_ENABLED", True)
    monkeypatch.setattr(memory_service.settings, "MINDMEMOS_RERANK_MODEL", "bge-reranker")
    monkeypatch.setattr(memory_service.settings, "MINDMEMOS_RERANK_API_BASE", "http://rerank:8000/v1")
    monkeypatch.setattr(memory_service.settings, "MINDMEMOS_RERANK_API_KEY", "sk-rerank")

    config = memory_service.get_user_memory_config(db, user)

    assert config.mindmemos_rerank is True
    assert config.mindmemos_score_threshold == 0.65


def test_user_memory_config_clears_threshold_when_rerank_is_disabled(db: Session):
    user = create_random_user(db)

    updated = memory_service.update_user_memory_config(
        db,
        user,
        MemoryConfigUpdate(mindmemos_rerank=False, mindmemos_score_threshold=0.8),
    )

    assert updated.mindmemos_rerank is False
    assert updated.mindmemos_score_threshold is None


def test_project_memory_config_is_project_scoped(db: Session):
    owner = create_random_user(db)
    outsider = create_random_user(db)
    project = _create_project(db, owner.id)

    config = memory_service.get_project_memory_config(db, project.id, owner)
    assert config.project_id == project.id
    assert config.include_project_memory is False
    assert config.project_memory_limit == 0
    assert config.include_user_memory is False
    assert config.user_memory_limit == 0

    updated = memory_service.update_project_memory_config(
        db,
        project.id,
        owner,
        MemoryConfigUpdate(include_task_memory=False, task_memory_limit=1),
    )
    assert updated.include_task_memory is False
    assert updated.task_memory_limit == 1

    with pytest.raises(HTTPException) as exc:
        memory_service.get_project_memory_config(db, project.id, outsider)
    assert exc.value.status_code == 403


def test_scoped_memory_cards_use_distinct_mindmemos_sessions(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    project = _create_project(db, user.id)
    task = models.Task(name="Scoped Task", project_id=project.id, input_args={})
    db.add(task)
    db.commit()
    db.refresh(task)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    calls, _store = _fake_mindmemos(monkeypatch)

    user_card = memory_service.upsert_memory_card(
        db,
        current_user=user,
        scope="user",
        request=MemoryCardUpsertRequest(
            type="general_insight",
            title="Global rule",
            content="Prefer robust heuristics.",
        ),
    )
    project_card = memory_service.upsert_memory_card(
        db,
        current_user=user,
        scope="project",
        project_id=project.id,
        request=MemoryCardUpsertRequest(
            type="domain_knowledge",
            title="Project rule",
            content="This benchmark rewards short tours.",
        ),
    )
    task_card = memory_service.upsert_memory_card(
        db,
        current_user=user,
        scope="task",
        task_id=task.id,
        request=MemoryCardUpsertRequest(
            type="good_algorithm",
            title="Task rule",
            content="Seed with nearest neighbor.",
        ),
    )

    add_payloads = [payload for path, payload in calls if path == "/v1/memory/add"]
    assert [payload["session_id"] for payload in add_payloads] == [
        "global",
        str(project.id),
        str(task.id),
    ]
    assert [payload["agent_id"] for payload in add_payloads] == ["global", "project", "task"]
    assert all("llm4ad_scope" not in payload["metadata"] for payload in add_payloads)
    assert all("project_id" not in payload["metadata"] for payload in add_payloads)
    assert all("task_id" not in payload["metadata"] for payload in add_payloads)

    assert [card.id for card in memory_service.list_memory_cards(db, user, scope="user").items] == [
        user_card.id
    ]
    assert [
        card.id
        for card in memory_service.list_memory_cards(
            db,
            user,
            scope="project",
            project_id=project.id,
        ).items
    ] == [project_card.id]
    assert [
        card.id
        for card in memory_service.list_memory_cards(db, user, scope="task", task_id=task.id).items
    ] == [task_card.id]


def test_project_memory_card_status_and_delete_are_project_scoped(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    project = _create_project(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    calls, store = _fake_mindmemos(monkeypatch)

    card = memory_service.upsert_memory_card(
        db,
        current_user=user,
        scope="project",
        project_id=project.id,
        request=MemoryCardUpsertRequest(
            type="domain_knowledge",
            title="Project rule",
            content="This project should keep project-only memories isolated.",
        ),
    )

    disabled = memory_service.update_memory_card_status(
        db,
        current_user=user,
        scope="project",
        project_id=project.id,
        memory_id=card.id,
        enabled=False,
    )

    assert disabled.enabled is False
    update_payload = [payload for path, payload in calls if path == "/v1/memory/update"][-1]
    assert update_payload["session_id"] == str(project.id)
    assert update_payload["agent_id"] == "project"
    assert update_payload["memory_id"] == card.id
    assert update_payload["status"] == "archived"

    memory_service.delete_memory_card(
        db,
        current_user=user,
        scope="project",
        project_id=project.id,
        memory_id=card.id,
    )

    delete_payload = [payload for path, payload in calls if path == "/v1/memory/delete"][-1]
    assert delete_payload == {"memory_id": card.id}
    assert card.id not in store


def test_memory_card_list_filters_include_remote_scope(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    project = _create_project(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    calls, _store = _fake_mindmemos(monkeypatch)

    memory_service.upsert_memory_card(
        db,
        current_user=user,
        scope="project",
        project_id=project.id,
        request=MemoryCardUpsertRequest(
            type="domain_knowledge",
            title="Project-only rule",
            content="Project memory must not include global memory.",
        ),
    )

    calls.clear()
    memory_service.list_memory_cards_page(
        db,
        user,
        scope="project",
        project_id=project.id,
        page=1,
        page_size=10,
    )

    project_list_payloads = [payload for path, payload in calls if path == "/v1/memory/list"]
    assert len(project_list_payloads) >= 2
    for payload in project_list_payloads:
        _assert_scope_payload(payload, user, session_id=str(project.id), agent_id="project")
        _assert_scope_filter(payload["filters"], user, session_id=str(project.id), agent_id="project")
    assert project_list_payloads[0]["filters"]["property_name"] == memory_service.LLM4AD_MEMORY_CARD_PROPERTY_FILTER
    assert project_list_payloads[1]["filters"]["property_name"] == memory_service.LLM4AD_MEMORY_TAG_PROPERTY

    calls.clear()
    memory_service.list_memory_cards_page(db, user, scope="user", page=1, page_size=10)

    user_list_payloads = [payload for path, payload in calls if path == "/v1/memory/list"]
    assert user_list_payloads
    for payload in user_list_payloads:
        _assert_scope_payload(payload, user, session_id="global", agent_id="global")
        _assert_scope_filter(payload["filters"], user, session_id="global", agent_id="global")


def test_memory_card_fetch_by_id_filters_include_remote_scope(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    project = _create_project(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    calls, _store = _fake_mindmemos(monkeypatch)

    card = memory_service.upsert_memory_card(
        db,
        current_user=user,
        scope="project",
        project_id=project.id,
        request=MemoryCardUpsertRequest(
            type="domain_knowledge",
            title="Project scoped card",
            content="Fetch by id should remain project scoped.",
        ),
    )

    calls.clear()
    memory_service.update_memory_card_status(
        db,
        current_user=user,
        scope="project",
        project_id=project.id,
        memory_id=card.id,
        enabled=False,
    )

    fetch_payloads = [
        payload
        for path, payload in calls
        if path == "/v1/memory/list" and payload["filters"].get("memory_id") == {"in": [card.id]}
    ]
    assert fetch_payloads
    for payload in fetch_payloads:
        _assert_scope_payload(payload, user, session_id=str(project.id), agent_id="project")
        _assert_scope_filter(payload["filters"], user, session_id=str(project.id), agent_id="project")


def test_create_task_merges_user_and_project_memory_defaults(db: Session, monkeypatch):
    user = create_random_user(db)
    project = _create_project(db, user.id)
    _enable_system_mindmemos(monkeypatch)

    memory_service.update_user_memory_config(
        db,
        user,
        MemoryConfigUpdate(user_memory_limit=3, project_memory_limit=4, task_memory_limit=5),
    )
    user_config = db.exec(
        select(models.UserMemoryConfig).where(models.UserMemoryConfig.user_id == user.id)
    ).one()
    user_config.mindmemos_binding_id = "pb_test"
    db.add(user_config)
    db.commit()
    memory_service.update_project_memory_config(
        db,
        project.id,
        user,
        MemoryConfigUpdate(project_memory_limit=2, include_task_memory=False),
    )

    task = task_service.create_task(
        db,
        task_schemas.TaskCreate(name="Merged Memory Task", project_id=project.id),
        user,
    )

    memory = task.input_args["memory"]
    assert memory["enabled"] is True
    assert memory["type"] == "mindmemos_cloud"
    assert memory["include_user_memory"] is False
    assert memory["include_project_memory"] is False
    assert memory["include_task_memory"] is False
    assert memory["user_memory_limit"] == 3
    assert memory["project_memory_limit"] == 2
    assert memory["task_memory_limit"] == 5
    assert memory["mindmemos_score_threshold"] is None


def test_create_task_omits_threshold_when_project_rerank_is_disabled(
    db: Session,
    monkeypatch,
):
    user = create_random_user(db)
    project = _create_project(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)
    project_config = db.exec(
        select(models.ProjectMemoryConfig).where(models.ProjectMemoryConfig.project_id == project.id)
    ).first()
    if project_config is None:
        memory_service.get_project_memory_config(db, project.id, user)
        project_config = db.exec(
            select(models.ProjectMemoryConfig).where(models.ProjectMemoryConfig.project_id == project.id)
        ).one()
    project_config.mindmemos_score_threshold = None
    db.add(project_config)
    db.commit()

    task = task_service.create_task(
        db,
        task_schemas.TaskCreate(name="No Threshold Task", project_id=project.id),
        user,
    )

    assert task.input_args["memory"]["mindmemos_score_threshold"] is None


def test_create_task_respects_explicit_local_yaml_memory_when_mindmemos_available(
    db: Session,
    monkeypatch,
):
    user = create_random_user(db)
    project = _create_project(db, user.id)
    _enable_system_mindmemos(monkeypatch)
    _mark_user_memory_bound(db, user.id)

    task = task_service.create_task(
        db,
        task_schemas.TaskCreate(
            name="Local Memory Task",
            project_id=project.id,
            input_args={
                "memory": {
                    "enabled": True,
                    "type": "local_yaml",
                    "max_entries": 12,
                },
            },
        ),
        user,
    )

    memory = task.input_args["memory"]
    assert memory["enabled"] is True
    assert memory["type"] == "local_yaml"
    assert memory["max_entries"] == 12
    assert "include_user_memory" not in memory
    assert "include_project_memory" not in memory
    assert "include_task_memory" not in memory
    assert "mindmemos_search_strategy" not in memory
    assert "mindmemos_base_url" not in memory
    assert "mindmemos_api_key" not in memory


def test_memory_health_reports_missing_system_config(db: Session, monkeypatch):
    user = create_random_user(db)
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_ENABLED", True)
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_BASE_URL", "http://mindmemos-api:8000")
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_JWT_SECRET", "")
    client = _FakeHttpClient([])

    result = memory_service.get_mindmemos_health(user, http_client_factory=lambda **_: client)

    assert result.ok is False
    assert result.service_reachable is False
    assert result.auth_ok is False
    assert "LLM4AD_MINDMEMOS_JWT_SECRET" in result.details["missing"]
    assert client.requests == []


def test_memory_health_uses_liveness_probe_only(monkeypatch):
    user = SimpleNamespace(id=uuid.uuid4())
    _enable_system_mindmemos(monkeypatch)
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_BASE_URL", "http://mindmemos-api:8000")
    client = _FakeHttpClient([_response(200, {"status": "ok"})])

    result = memory_service.get_mindmemos_health(user, http_client_factory=lambda **_: client)

    assert result.ok is True
    assert result.service_reachable is True
    assert result.auth_ok is True
    assert result.message == "MindMemOS service is ready."
    assert client.requests == [("GET", "http://mindmemos-api:8000/healthz", None)]
