import importlib.util
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models import LLMProvider, ProviderType


def _load_execution_module():
    package_name = "app.services.task_service"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = []  # type: ignore[attr-defined]
        sys.modules[package_name] = package

    auth_module_name = f"{package_name}.auth"
    if auth_module_name not in sys.modules:
        auth_module = types.ModuleType(auth_module_name)
        auth_module.get_task_with_auth = lambda *_args, **_kwargs: None
        sys.modules[auth_module_name] = auth_module

    execution_path_from_env = os.environ.get("TASK_SERVICE_EXECUTION_PATH")
    if execution_path_from_env:
        execution_path = Path(execution_path_from_env)
    else:
        execution_path = (
            Path(__file__).resolve().parents[2]
            / "app"
            / "services"
            / "task_service"
            / "execution.py"
        )
    spec = importlib.util.spec_from_file_location(
        f"{package_name}.execution_under_test",
        execution_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


task_service = _load_execution_module()


class _ExecResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, providers):
        self._providers = providers

    def exec(self, _statement):
        return _ExecResult(self._providers)


def test_resolve_providers_uses_dynamic_builtin_models_for_default_slots(monkeypatch):
    user_id = uuid4()
    provider_id = uuid4()
    provider = LLMProvider(
        id=provider_id,
        user_id=None,
        name="Builtin LiteLLM",
        type=ProviderType.OPENAI_COMPATIBLE,
        api_key="sk-test",
        base_url="https://gateway.example.com/v1",
        model="stale-db-model",
        is_builtin=True,
        visible_to_all=True,
    )
    default_model = SimpleNamespace(
        planner_provider_id=provider_id,
        planner_model_name="deepseek-v4-flash",
        coder_provider_id=provider_id,
        coder_model_name="deepseek-v4-flash",
        other_provider_id=provider_id,
        other_model_name="deepseek-v4-flash",
    )

    def fake_get_user_default_model(_db, requested_user_id, access_token=None):
        assert requested_user_id == user_id
        assert access_token == "user-token"
        return default_model

    monkeypatch.setattr(
        "app.services.user_default_model_service.get_user_default_model",
        fake_get_user_default_model,
    )
    monkeypatch.setattr(
        "app.services.provider_service.fetch_builtin_provider_models",
        lambda _provider, _access_token, user_id=None: ["deepseek-v4-flash"],
    )

    resolved = task_service._resolve_providers(
        _FakeDB([provider]),
        {
            "planner": {"provider": "default", "provider_model": ""},
            "coder": {"provider": "default", "provider_model": ""},
            "evaluator": {"provider": "default", "provider_model": ""},
        },
        SimpleNamespace(id=user_id),
        access_token="user-token",
    )

    expected_provider_name = f"{provider_id}deepseek-v4-flash"
    assert resolved["planner"]["provider"] == expected_provider_name
    assert resolved["coder"]["provider"] == expected_provider_name
    assert resolved["evaluator"]["provider"] == expected_provider_name
    assert [p["name"] for p in resolved["providers"]] == [expected_provider_name]
    assert all(p["model"] != "stale-db-model" for p in resolved["providers"])


def test_resolve_providers_routes_embedding_through_forced_litellm_gateway(monkeypatch):
    user_id = uuid4()
    provider_id = uuid4()
    provider = LLMProvider(
        id=provider_id,
        user_id=None,
        name="Builtin LiteLLM",
        type=ProviderType.OPENAI_COMPATIBLE,
        api_key="sk-test",
        base_url="https://stale-provider.example.com/v1",
        model="deepseek-v4-flash",
        is_builtin=True,
        visible_to_all=True,
    )

    default_model = SimpleNamespace(
        planner_provider_id=provider_id,
        planner_model_name="deepseek-v4-flash",
        coder_provider_id=provider_id,
        coder_model_name="deepseek-v4-flash",
        other_provider_id=provider_id,
        other_model_name="deepseek-v4-flash",
    )

    monkeypatch.setattr(task_service.settings, "TEAM_ID", "team-123", raising=False)
    monkeypatch.setattr(
        task_service.settings,
        "BUILTIN_PROVIDER_BASE_URL",
        "https://external-provider.example.com/v1",
        raising=False,
    )
    monkeypatch.setattr(task_service.settings, "LITELLM_GATEWAY_BASE_URL", "http://gateway:9090", raising=False)
    monkeypatch.setattr(
        task_service.settings,
        "BUILTIN_PROVIDER_API_KEY",
        "real-gateway-key",
        raising=False,
    )
    monkeypatch.setattr(
        task_service.settings,
        "JINA_API_KEY",
        "real-jina-key",
        raising=False,
    )
    monkeypatch.setattr(
        task_service.settings,
        "EMBEDDING_MODEL",
        "jina-embeddings-v4",
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.user_default_model_service.get_user_default_model",
        lambda _db, _user_id, access_token=None: default_model,
    )
    monkeypatch.setattr(
        "app.services.provider_service.fetch_builtin_provider_models",
        lambda _provider, _access_token, user_id=None: ["deepseek-v4-flash"],
    )

    resolved = task_service._resolve_providers(
        _FakeDB([provider]),
        {
            "planner": {"provider": "default", "provider_model": ""},
            "coder": {"provider": "default", "provider_model": ""},
            "evaluator": {"provider": "default", "provider_model": ""},
        },
        SimpleNamespace(id=user_id),
        access_token="user-token",
    )

    assert resolved["embedding"]["base_url"] == (
        "http://gateway:9090/litellm_proxy/team-123/user-token/v1"
    )
    assert resolved["embedding"]["api_key"] == "EMPTY"
    assert resolved["embedding"]["model"] == "jina-embeddings-v4"


def test_resolve_providers_does_not_proxy_gateway_embedding(monkeypatch):
    user_id = uuid4()
    provider_id = uuid4()
    provider = LLMProvider(
        id=provider_id,
        user_id=None,
        name="Builtin LiteLLM",
        type=ProviderType.OPENAI_COMPATIBLE,
        api_key="sk-test",
        base_url="http://gateway:9090/litellm_proxy/${TEAM_ID}/{accessToken}/v1",
        model="deepseek-v4-flash",
        is_builtin=True,
        visible_to_all=True,
    )

    default_model = SimpleNamespace(
        planner_provider_id=provider_id,
        planner_model_name="deepseek-v4-flash",
        coder_provider_id=provider_id,
        coder_model_name="deepseek-v4-flash",
        other_provider_id=provider_id,
        other_model_name="deepseek-v4-flash",
    )

    issued_tokens = []

    def fake_issue_token(**kwargs):
        issued_tokens.append(kwargs)
        return f"proxy-token-{len(issued_tokens)}"

    monkeypatch.setattr(task_service.settings, "TEAM_ID", "team-123", raising=False)
    monkeypatch.setattr(task_service.settings, "LITELLM_GATEWAY_BASE_URL", "http://gateway:9090", raising=False)
    monkeypatch.setattr(
        task_service.settings,
        "BUILTIN_PROVIDER_BASE_URL",
        "",
        raising=False,
    )
    monkeypatch.setattr(
        task_service.settings,
        "BUILTIN_PROVIDER_API_KEY",
        "real-gateway-key",
        raising=False,
    )
    monkeypatch.setattr(
        task_service.settings,
        "LLM_PROXY_ENABLE",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        task_service.settings,
        "LLM_PROXY_BASE_URL",
        "http://backend:8000/api/v1/llm4ad/llmproxy",
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.user_default_model_service.get_user_default_model",
        lambda _db, _user_id, access_token=None: default_model,
    )
    monkeypatch.setattr(
        "app.services.provider_service.fetch_builtin_provider_models",
        lambda _provider, _access_token, user_id=None: ["deepseek-v4-flash"],
    )
    monkeypatch.setattr("app.services.credential_broker.issue_token", fake_issue_token)

    resolved = task_service._resolve_providers(
        _FakeDB([provider]),
        {
            "planner": {"provider": "default", "provider_model": ""},
            "coder": {"provider": "default", "provider_model": ""},
            "evaluator": {"provider": "default", "provider_model": ""},
        },
        SimpleNamespace(id=user_id),
        access_token="user-token",
        task_id=uuid4(),
    )

    assert [provider["base_url"] for provider in resolved["providers"]] == [
        "http://backend:8000/api/v1/llm4ad/llmproxy"
    ]
    assert resolved["embedding"]["base_url"] == (
        "http://gateway:9090/litellm_proxy/team-123/user-token/v1"
    )
    assert resolved["embedding"]["api_key"] == "EMPTY"
    assert [token["model"] for token in issued_tokens] == ["deepseek-v4-flash"]


def test_resolve_providers_requires_gateway_embedding_context(monkeypatch):
    user_id = uuid4()
    provider_id = uuid4()
    provider = LLMProvider(
        id=provider_id,
        user_id=None,
        name="Builtin LiteLLM",
        type=ProviderType.OPENAI_COMPATIBLE,
        api_key="sk-test",
        base_url="http://gateway:9090/litellm_proxy/${TEAM_ID}/{accessToken}/v1",
        model="deepseek-v4-flash",
        is_builtin=True,
        visible_to_all=True,
    )

    default_model = SimpleNamespace(
        planner_provider_id=provider_id,
        planner_model_name="deepseek-v4-flash",
        coder_provider_id=provider_id,
        coder_model_name="deepseek-v4-flash",
        other_provider_id=provider_id,
        other_model_name="deepseek-v4-flash",
    )

    monkeypatch.setattr(task_service.settings, "TEAM_ID", "", raising=False)
    monkeypatch.setattr(task_service.settings, "LITELLM_GATEWAY_BASE_URL", "http://gateway:9090", raising=False)
    monkeypatch.setattr(
        "app.services.user_default_model_service.get_user_default_model",
        lambda _db, _user_id, access_token=None: default_model,
    )
    monkeypatch.setattr(
        "app.services.provider_service.fetch_builtin_provider_models",
        lambda _provider, _access_token, user_id=None: ["deepseek-v4-flash"],
    )

    with pytest.raises(HTTPException) as exc_info:
        task_service._resolve_providers(
            _FakeDB([provider]),
            {
                "planner": {"provider": "default", "provider_model": ""},
                "coder": {"provider": "default", "provider_model": ""},
                "evaluator": {"provider": "default", "provider_model": ""},
            },
            SimpleNamespace(id=user_id),
            access_token="user-token",
        )

    assert exc_info.value.status_code == 500
    assert "TEAM_ID" in exc_info.value.detail
