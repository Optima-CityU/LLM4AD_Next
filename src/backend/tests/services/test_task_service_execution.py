import importlib.util
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.core.security import decode_access_token
from app.models import (
    EmbeddingMode,
    EmbeddingProvider,
    EmbeddingProviderType,
    LLMProvider,
    ProviderType,
)


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
    def __init__(self, providers, embedding_provider=None):
        self._providers = providers
        self._embedding_provider = embedding_provider

    def exec(self, _statement):
        return _ExecResult(self._providers)

    def get(self, model, item_id):
        if model is EmbeddingProvider and self._embedding_provider and self._embedding_provider.id == item_id:
            return self._embedding_provider
        return None


def test_resolve_providers_issues_gateway_token_without_http_context(monkeypatch):
    user_id = uuid4()
    provider_id = uuid4()
    embedding_provider_id = uuid4()
    gateway_url = "http://gateway:9090/litellm_proxy/${TEAM_ID}/{accessToken}/v1"
    provider = LLMProvider(
        id=provider_id,
        user_id=None,
        name="Builtin LiteLLM",
        type=ProviderType.OPENAI_COMPATIBLE,
        api_key="EMPTY",
        base_url=gateway_url,
        model="test-model",
        is_builtin=True,
        visible_to_all=True,
    )
    embedding_provider = EmbeddingProvider(
        id=embedding_provider_id,
        user_id=None,
        name="Builtin Embedding",
        type=EmbeddingProviderType.OPENAI_COMPATIBLE,
        mode=EmbeddingMode.SHARED,
        api_key="EMPTY",
        base_url=gateway_url,
        model="test-embedding",
        dim=1536,
        is_builtin=True,
        visible_to_all=True,
    )
    defaults = SimpleNamespace(
        planner_provider_id=provider_id,
        planner_model_name="test-model",
        coder_provider_id=provider_id,
        coder_model_name="test-model",
        other_provider_id=provider_id,
        other_model_name="test-model",
        embedding_enabled=True,
        embedding_provider_id=embedding_provider_id,
    )
    observed_tokens = []

    monkeypatch.setattr(task_service.settings, "TEAM_ID", "team-123", raising=False)
    monkeypatch.setattr(
        "app.services.user_default_model_service.get_user_default_model",
        lambda _db, _user_id, access_token=None: defaults,
    )

    def fake_fetch(_provider, access_token, **_kwargs):
        observed_tokens.append(access_token)
        return ["test-model"]

    monkeypatch.setattr(
        "app.services.provider_service.fetch_builtin_provider_models", fake_fetch
    )

    resolved = task_service._resolve_providers(
        _FakeDB([provider], embedding_provider),
        {
            "planner": {"provider": "default", "provider_model": ""},
            "coder": {"provider": "default", "provider_model": ""},
            "evaluator": {"provider": "default", "provider_model": ""},
        },
        SimpleNamespace(id=user_id),
        access_token=None,
    )

    assert len(observed_tokens) == 1
    gateway_token = observed_tokens[0]
    assert gateway_token
    payload = decode_access_token(gateway_token)
    assert payload is not None
    assert payload["sub"] == str(user_id)
    expected_url = f"http://gateway:9090/litellm_proxy/team-123/{gateway_token}/v1"
    assert resolved["providers"][0]["base_url"] == expected_url
    assert resolved["embedding"]["base_url"] == expected_url


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


def test_resolve_providers_builds_builtin_split_embedding_through_litellm_gateway(monkeypatch):
    user_id = uuid4()
    provider_id = uuid4()
    embedding_provider_id = uuid4()
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
    embedding_provider = EmbeddingProvider(
        id=embedding_provider_id,
        user_id=None,
        name="Builtin Embedding",
        type=EmbeddingProviderType.LOCAL,
        mode=EmbeddingMode.SPLIT,
        is_builtin=True,
        visible_to_all=True,
        text_type=EmbeddingProviderType.OPENAI_COMPATIBLE,
        text_base_url="http://gateway:9090/litellm_proxy/${TEAM_ID}/{accessToken}/v1",
        text_api_key="EMPTY",
        text_model="jina-text",
        text_task="text-matching",
        code_type=EmbeddingProviderType.OPENAI_COMPATIBLE,
        code_base_url="http://gateway:9090/litellm_proxy/${TEAM_ID}/{accessToken}/v1",
        code_api_key="EMPTY",
        code_model="jina-code",
        code_task="code.passage",
        dim=2048,
    )

    default_model = SimpleNamespace(
        planner_provider_id=provider_id,
        planner_model_name="deepseek-v4-flash",
        coder_provider_id=provider_id,
        coder_model_name="deepseek-v4-flash",
        other_provider_id=provider_id,
        other_model_name="deepseek-v4-flash",
        embedding_enabled=True,
        embedding_provider_id=embedding_provider_id,
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
        "app.services.user_default_model_service.get_user_default_model",
        lambda _db, _user_id, access_token=None: default_model,
    )
    monkeypatch.setattr(
        "app.services.provider_service.fetch_builtin_provider_models",
        lambda _provider, _access_token, user_id=None: ["deepseek-v4-flash"],
    )

    resolved = task_service._resolve_providers(
        _FakeDB([provider], embedding_provider),
        {
            "planner": {"provider": "default", "provider_model": ""},
            "coder": {"provider": "default", "provider_model": ""},
            "evaluator": {"provider": "default", "provider_model": ""},
        },
        SimpleNamespace(id=user_id),
        access_token="user-token",
    )

    assert resolved["embedding"]["type"] == "local"
    assert resolved["embedding"]["text_config"]["base_url"] == (
        "http://gateway:9090/litellm_proxy/team-123/user-token/v1"
    )
    assert resolved["embedding"]["code_config"]["base_url"] == (
        "http://gateway:9090/litellm_proxy/team-123/user-token/v1"
    )
    assert resolved["embedding"]["text_config"]["api_key"] == "EMPTY"
    assert resolved["embedding"]["code_config"]["api_key"] == "EMPTY"
    assert resolved["embedding"]["text_config"]["model"] == "jina-text"
    assert resolved["embedding"]["code_config"]["model"] == "jina-code"


def test_resolve_providers_does_not_proxy_gateway_embedding(monkeypatch):
    user_id = uuid4()
    provider_id = uuid4()
    embedding_provider_id = uuid4()
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
    embedding_provider = EmbeddingProvider(
        id=embedding_provider_id,
        user_id=None,
        name="Builtin Embedding",
        type=EmbeddingProviderType.LOCAL,
        mode=EmbeddingMode.SPLIT,
        is_builtin=True,
        visible_to_all=True,
        text_type=EmbeddingProviderType.OPENAI_COMPATIBLE,
        text_base_url="http://gateway:9090/litellm_proxy/${TEAM_ID}/{accessToken}/v1",
        text_api_key="EMPTY",
        text_model="jina-text",
        code_type=EmbeddingProviderType.OPENAI_COMPATIBLE,
        code_base_url="http://gateway:9090/litellm_proxy/${TEAM_ID}/{accessToken}/v1",
        code_api_key="EMPTY",
        code_model="jina-code",
        dim=2048,
    )

    default_model = SimpleNamespace(
        planner_provider_id=provider_id,
        planner_model_name="deepseek-v4-flash",
        coder_provider_id=provider_id,
        coder_model_name="deepseek-v4-flash",
        other_provider_id=provider_id,
        other_model_name="deepseek-v4-flash",
        embedding_enabled=True,
        embedding_provider_id=embedding_provider_id,
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
        _FakeDB([provider], embedding_provider),
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
    assert resolved["embedding"]["text_config"]["base_url"] == (
        "http://gateway:9090/litellm_proxy/team-123/user-token/v1"
    )
    assert resolved["embedding"]["code_config"]["base_url"] == (
        "http://gateway:9090/litellm_proxy/team-123/user-token/v1"
    )
    assert resolved["embedding"]["text_config"]["api_key"] == "EMPTY"
    assert resolved["embedding"]["code_config"]["api_key"] == "EMPTY"
    assert [token["model"] for token in issued_tokens] == ["deepseek-v4-flash"]


def test_resolve_providers_omits_embedding_when_default_embedding_disabled(monkeypatch):
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
        embedding_enabled=False,
        embedding_provider_id=None,
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

    assert "embedding" not in resolved
