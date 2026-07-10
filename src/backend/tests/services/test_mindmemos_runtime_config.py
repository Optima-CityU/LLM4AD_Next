"""Tests for system-level MindMemOS runtime configuration."""

import uuid

from app.core.config import settings
from app.services.task_service import execution


class _User:
    id = uuid.UUID("11111111-1111-1111-1111-111111111111")


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
    assert memory["mindmemos_user_id"] == str(_User.id)
    assert memory["mindmemos_project_id"] == str(project_id)
    assert memory["mindmemos_session_id"] == str(task_id)
    assert memory["mindmemos_app_id"] == "llm4ad-test"
    assert memory["mindmemos_agent_id"] == "task"
    assert memory["mindmemos_fail_open"] is True
    assert memory["include_project_memory"] is False
    assert memory["include_task_memory"] is True
    assert memory["static_cards"] == [{"id": "legacy", "content": "local"}]


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
