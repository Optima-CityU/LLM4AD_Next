"""Tests for user/project memory defaults and scoped memory cards."""

import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app import models
from app.core.db import engine
from app.schemas.memory import MemoryCardUpsertRequest, MemoryConfigUpdate
from app.schemas import task as task_schemas
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
    store: dict[str, tuple[dict, str]] = {}

    def fake_post(_current_user, path: str, payload: dict, *, scopes: list[str]):
        calls.append((path, payload))
        if path == "/v1/memory/list":
            session_id = payload["session_id"]
            memories = [
                {"id": memory_id, "memory": content, "metadata": scope.get("metadata", {})}
                for memory_id, (scope, content) in store.items()
                if scope["session_id"] == session_id
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
            store[memory_id] = (payload, payload["messages"][0]["content"])
            return {"code": "ok", "data": {"memories": [{"memory_id": memory_id}]}}
        if path == "/v1/memory/update":
            existing_scope = store[payload["memory_id"]][0]
            store[payload["memory_id"]] = (existing_scope, payload["content"])
            return {"code": "ok", "data": None}
        if path == "/v1/memory/delete":
            assert payload["hard"] is True
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


def test_user_memory_config_defaults_and_updates(db: Session):
    user = create_random_user(db)

    config = memory_service.get_user_memory_config(db, user)
    assert config.enabled is True
    assert config.include_user_memory is True
    assert config.include_project_memory is True
    assert config.include_task_memory is True
    assert config.user_memory_limit == 5

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


def test_project_memory_config_is_project_scoped(db: Session):
    owner = create_random_user(db)
    outsider = create_random_user(db)
    project = _create_project(db, owner.id)

    config = memory_service.get_project_memory_config(db, project.id, owner)
    assert config.project_id == project.id
    assert config.include_project_memory is True

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
    assert [payload["metadata"]["llm4ad_scope"] for payload in add_payloads] == [
        "user",
        "project",
        "task",
    ]

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
    assert memory["include_user_memory"] is True
    assert memory["include_project_memory"] is True
    assert memory["include_task_memory"] is False
    assert memory["user_memory_limit"] == 3
    assert memory["project_memory_limit"] == 2
    assert memory["task_memory_limit"] == 5


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


def test_memory_health_reports_invalid_api_key(db: Session, monkeypatch):
    user = create_random_user(db)
    _enable_system_mindmemos(monkeypatch)
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_BASE_URL", "http://mindmemos-api:8000")
    client = _FakeHttpClient([
        _response(200, {"status": "ok"}),
        _response(401, {"code": "auth.invalid_api_key", "message": "invalid api key"}),
    ])

    result = memory_service.get_mindmemos_health(user, http_client_factory=lambda **_: client)

    assert result.ok is False
    assert result.service_reachable is True
    assert result.auth_ok is False
    assert result.error_code == "auth.invalid_api_key"
    assert "invalid api key" in result.message


def test_memory_health_reports_ready_service(db: Session, monkeypatch):
    user = create_random_user(db)
    _enable_system_mindmemos(monkeypatch)
    monkeypatch.setattr(memory_service.settings, "LLM4AD_MINDMEMOS_BASE_URL", "http://mindmemos-api:8000")
    client = _FakeHttpClient([
        _response(200, {"status": "ok"}),
        _response(200, {"code": "ok", "data": {"memories": []}}),
    ])

    result = memory_service.get_mindmemos_health(user, http_client_factory=lambda **_: client)

    assert result.ok is True
    assert result.service_reachable is True
    assert result.auth_ok is True
    assert result.message == "MindMemOS service is ready."
    assert client.requests[1][2]["headers"]["Authorization"].startswith("Bearer ")
