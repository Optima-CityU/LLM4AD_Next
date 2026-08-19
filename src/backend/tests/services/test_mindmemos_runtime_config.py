"""Tests for system-level MindMemOS runtime configuration."""

import base64
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import yaml

from app.core.config import settings
from app.services import memory_service
from app.services.task_service import crud, execution


class _User:
    id = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _decode_jwt_payload(token: str) -> dict:
    encoded_payload = token.split(".")[1]
    encoded_payload += "=" * (-len(encoded_payload) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded_payload))


def test_gateway_tokens_always_select_structured_memory(monkeypatch):
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_JWT_SECRET", "jwt-test-secret")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_JWT_ISSUER", "llm4ad-test")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_JWT_AUDIENCE", "mindmemos-test")

    token = memory_service._mindmemos_gateway_token(
        _User(),
        scopes=["memory:read", "memory:write"],
    )
    payload = _decode_jwt_payload(token)

    assert payload["memory_algorithm"] == "structured"


def test_gateway_headers_always_select_structured_memory(monkeypatch):
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_JWT_SECRET", "jwt-test-secret")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_JWT_ISSUER", "llm4ad-test")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_JWT_AUDIENCE", "mindmemos-test")

    headers = memory_service._mindmemos_headers(
        _User(),
        scopes=["memory:read"],
    )

    assert _decode_jwt_payload(headers["Authorization"].removeprefix("Bearer "))[
        "memory_algorithm"
    ] == "structured"


def test_memory_post_uses_structured_for_every_llm4ad_scope(monkeypatch):
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_BASE_URL", "http://mindmemos.test")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_JWT_SECRET", "jwt-test-secret")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_JWT_ISSUER", "llm4ad-test")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_JWT_AUDIENCE", "mindmemos-test")
    captured_headers: list[dict[str, str]] = []
    captured_payloads: list[dict] = []

    class _Response:
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {"code": "ok", "data": {"memories": []}}

    class _Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, _url, *, headers, json):
            captured_headers.append(headers)
            captured_payloads.append(dict(json))
            return _Response()

    monkeypatch.setattr(memory_service.httpx, "Client", _Client)

    memory_service._mindmemos_post(
        _User(),
        "/v1/memory/list",
        {"agent_id": "task"},
        scopes=["memory:read"],
    )
    memory_service._mindmemos_post(
        _User(),
        "/v1/memory/list",
        {"agent_id": "project"},
        scopes=["memory:read"],
    )
    memory_service._remote_delete_card(
        _User(),
        "task-memory",
        scope_data={"agent_id": "task"},
    )
    memory_service._remote_delete_card(
        _User(),
        "project-memory",
        scope_data={"agent_id": "project"},
    )

    algorithms = [
        _decode_jwt_payload(headers["Authorization"].removeprefix("Bearer "))[
            "memory_algorithm"
        ]
        for headers in captured_headers
    ]
    assert algorithms == ["structured", "structured", "structured", "structured"]
    assert captured_payloads[-2:] == [
        {"memory_id": "task-memory", "hard": True},
        {"memory_id": "project-memory", "hard": True},
    ]


def test_mock_tsp_template_disables_remote_memory(monkeypatch):
    """The offline mock template must never inherit a MindMemOS binding."""
    config_path = (
        Path(__file__).resolve().parents[4]
        / "examples/applications/tsp_benchmark_python_mock/config.yaml"
    )
    input_args = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    project_defaults = SimpleNamespace(
        mindmemos_binding_id="binding-valid",
        include_user_memory=True,
        include_project_memory=True,
        include_task_memory=True,
        user_memory_limit=2,
        project_memory_limit=2,
        task_memory_limit=2,
        mindmemos_search_strategy="fast",
        mindmemos_rerank=False,
        mindmemos_score_threshold=None,
        mindmemos_fail_open=True,
    )
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_ENABLED", True)
    monkeypatch.setattr(
        settings, "LLM4AD_MINDMEMOS_BASE_URL", "http://mindmemos-api:8000"
    )
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_JWT_SECRET", "jwt-test-secret")
    monkeypatch.setattr(
        memory_service, "get_project_memory_config", lambda *_args: project_defaults
    )
    monkeypatch.setattr(
        memory_service, "get_mindmemos_provider_binding_error", lambda *_args: None
    )

    resolved = crud._apply_memory_defaults(
        None,
        input_args,
        current_user=_User(),
        project_id=uuid.uuid4(),
    )
    execution._apply_mindmemos_runtime_config(
        resolved,
        current_user=_User(),
        task_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )

    assert resolved["memory"]["enabled"] is False
    assert resolved["memory"]["type"] == "local_yaml"
    assert "mindmemos_base_url" not in resolved["memory"]


def test_apply_memory_defaults_falls_back_from_invalid_provider_binding(monkeypatch):
    """Do not inject MindMemOS settings when a persisted embedding binding is invalid."""
    project_defaults = SimpleNamespace(
        mindmemos_binding_id="binding-invalid",
        include_user_memory=True,
        include_project_memory=True,
        include_task_memory=True,
        user_memory_limit=2,
        project_memory_limit=2,
        task_memory_limit=2,
        mindmemos_search_strategy="fast",
        mindmemos_rerank=False,
        mindmemos_score_threshold=None,
        mindmemos_fail_open=True,
    )
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_ENABLED", True)
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_BASE_URL", "http://mindmemos-api:8000")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_JWT_SECRET", "jwt-test-secret")
    monkeypatch.setattr(memory_service, "get_project_memory_config", lambda *_args: project_defaults)
    monkeypatch.setattr(
        memory_service,
        "get_mindmemos_provider_binding_error",
        lambda *_args: "Embedding 配置无效：API 地址必须是有效的 HTTP(S) URL",
    )

    input_args = crud._apply_memory_defaults(
        None,
        {},
        current_user=_User(),
        project_id=uuid.uuid4(),
    )

    assert input_args["memory"]["type"] == "local_yaml"


def test_apply_mindmemos_runtime_config_overrides_task_memory_when_enabled(
    monkeypatch,
):
    task_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    project_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
    input_args = {
        "memory": {
            "type": "mindmemos_cloud",
            "static_cards": [{"id": "legacy", "content": "local"}],
            "mindmemos_fail_open": True,
            "include_project_memory": False,
        }
    }
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_ENABLED", True)
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_BASE_URL", "http://mindmemos-api:8000/")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_JWT_SECRET", "jwt-test-secret")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_APP_ID", "llm4ad-test")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_AGENT_ID", "planner-test")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_FAIL_OPEN", False)
    monkeypatch.setattr(execution, "_mindmemos_task_token", lambda _current_user: "jwt-task-token")

    execution._apply_mindmemos_runtime_config(
        input_args,
        current_user=_User(),
        task_id=task_id,
        project_id=project_id,
    )

    memory = input_args["memory"]
    assert memory["type"] == "mindmemos_cloud"
    assert memory["mindmemos_base_url"] == "http://mindmemos-api:8000"
    assert memory["mindmemos_api_key"] == "jwt-task-token"
    assert "mindmemos_shared_api_key" not in memory
    assert memory["mindmemos_user_id"] == str(_User.id)
    assert memory["mindmemos_project_id"] == str(project_id)
    assert memory["mindmemos_session_id"] == str(task_id)
    assert memory["mindmemos_app_id"] == "llm4ad-test"
    assert memory["mindmemos_agent_id"] == "task"
    assert memory["mindmemos_fail_open"] is True
    assert memory["mindmemos_request_timeout"] == 300.0
    assert memory["mindmemos_add_timeout"] == 120.0
    assert memory["mindmemos_extraction_prompt_language"] == "auto"
    assert memory["include_project_memory"] is False
    assert memory["include_task_memory"] is True
    assert memory["static_cards"] == [{"id": "legacy", "content": "local"}]


def test_apply_mindmemos_runtime_config_uses_root_task_session_when_provided(
    monkeypatch,
):
    task_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    root_task_id = uuid.UUID("44444444-4444-4444-4444-444444444444")
    project_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
    input_args = {"memory": {"type": "mindmemos_cloud"}}
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_ENABLED", True)
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_BASE_URL", "http://mindmemos-api:8000")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_JWT_SECRET", "jwt-test-secret")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_APP_ID", "llm4ad-test")
    monkeypatch.setattr(execution, "_mindmemos_task_token", lambda _current_user: "jwt-task-token")

    execution._apply_mindmemos_runtime_config(
        input_args,
        current_user=_User(),
        task_id=task_id,
        project_id=project_id,
        memory_task_id=root_task_id,
    )

    assert input_args["memory"]["mindmemos_session_id"] == str(root_task_id)


def test_apply_mindmemos_runtime_config_leaves_memory_unchanged_when_disabled(
    monkeypatch,
):
    input_args = {"memory": {"type": "local_yaml", "max_entries": 12}}
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_ENABLED", False)

    execution._apply_mindmemos_runtime_config(
        input_args,
        current_user=_User(),
        task_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )

    assert input_args == {"memory": {"type": "local_yaml", "max_entries": 12}}


def test_apply_mindmemos_runtime_config_falls_back_without_gateway_secret(
    monkeypatch,
):
    input_args = {"memory": {"type": "local_yaml", "max_entries": 12}}
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_ENABLED", True)
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_BASE_URL", "http://mindmemos-api:8000")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_JWT_SECRET", "")

    execution._apply_mindmemos_runtime_config(
        input_args,
        current_user=_User(),
        task_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )

    assert input_args == {"memory": {"type": "local_yaml", "max_entries": 12}}


def test_apply_mindmemos_runtime_config_falls_back_without_base_url(
    monkeypatch,
):
    input_args = {"memory": {"type": "local_yaml", "max_entries": 12}}
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_ENABLED", True)
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_BASE_URL", "")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_JWT_SECRET", "jwt-test-secret")

    execution._apply_mindmemos_runtime_config(
        input_args,
        current_user=_User(),
        task_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )

    assert input_args == {"memory": {"type": "local_yaml", "max_entries": 12}}


def test_apply_mindmemos_runtime_config_respects_task_comparison_toggle(
    monkeypatch,
):
    input_args = {"memory": {"type": "local_yaml", "enabled": False}}
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_ENABLED", True)
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_BASE_URL", "http://mindmemos-api:8000")
    monkeypatch.setattr(settings, "LLM4AD_MINDMEMOS_JWT_SECRET", "jwt-test-secret")

    execution._apply_mindmemos_runtime_config(
        input_args,
        current_user=_User(),
        task_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )

    assert input_args == {"memory": {"type": "local_yaml", "enabled": False}}
