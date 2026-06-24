from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models import LLMProvider, ProviderType
from app.services import provider_service


def setup_function():
    clear_cache = getattr(provider_service, "_clear_provider_model_cache", None)
    if clear_cache is not None:
        clear_cache()


def test_litellm_team_models_are_fetched_through_gateway(monkeypatch):
    calls: list[tuple[str, dict[str, str]]] = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": ["gpt-4o", "gpt-4o-mini"]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url, headers):
            calls.append((url, headers))
            return FakeResponse()

    monkeypatch.setattr(provider_service.settings, "LITELLM_GATEWAY_BASE_URL", "http://gateway:9090")
    monkeypatch.setattr(provider_service.httpx, "Client", FakeClient)

    assert provider_service._fetch_litellm_team_models_via_gateway("user-token") == ["gpt-4o", "gpt-4o-mini"]
    assert calls == [
        (
            "http://gateway:9090/internal/litellm/team/models",
            {"Authorization": "Bearer user-token"},
        )
    ]


def test_litellm_team_models_do_not_use_direct_admin_settings(monkeypatch):
    monkeypatch.setattr(provider_service.settings, "LITELLM_GATEWAY_BASE_URL", "http://gateway:9090")

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        provider_service,
        "_fetch_gateway_json",
        lambda path, token: calls.append((path, token)) or {"data": ["team-model"]},
    )

    assert provider_service._fetch_litellm_team_models_via_gateway("user-token") == ["team-model"]
    assert calls == [("/internal/litellm/team/models", "user-token")]


def test_fetch_builtin_provider_models_prefers_gateway_team_models(monkeypatch):
    provider = SimpleNamespace(
        id=uuid4(),
        base_url="http://gateway:9090/litellm_proxy/team/{accessToken}/v1",
        api_key="sk-test",
    )
    calls: list[str] = []

    def fake_team_models(access_token):
        calls.append("internal-gateway")
        assert access_token == "user-token"
        return ["team-model"]

    def fake_proxy_models(*_args, **_kwargs):
        calls.append("proxy-models")
        return ["proxy-model"]

    monkeypatch.setattr(provider_service, "_fetch_litellm_team_models_via_gateway", fake_team_models)
    monkeypatch.setattr(provider_service, "_fetch_builtin_provider_models_via_gateway", fake_proxy_models)

    assert provider_service.fetch_builtin_provider_models(
        provider,
        access_token="user-token",
        user_id="user-a",
    ) == ["team-model"]
    assert calls == ["internal-gateway"]


def test_fetch_builtin_provider_models_falls_back_to_gateway_proxy_models(monkeypatch):
    provider = SimpleNamespace(
        id=uuid4(),
        base_url="http://gateway:9090/litellm_proxy/team/{accessToken}/v1",
        api_key="sk-test",
    )

    monkeypatch.setattr(provider_service, "_fetch_litellm_team_models_via_gateway", lambda _token: [])
    monkeypatch.setattr(
        provider_service,
        "_fetch_builtin_provider_models_via_gateway",
        lambda _provider, base_url, access_token: [base_url, access_token],
    )

    assert provider_service.fetch_builtin_provider_models(
        provider,
        access_token="user-token",
        user_id="user-a",
    ) == ["http://gateway:9090/litellm_proxy/team/user-token/v1", "user-token"]


def test_gateway_models_are_refetched_by_resolved_base_url(monkeypatch):
    calls: list[str] = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": f"gateway-model-{len(calls)}", "mode": "chat"}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url, headers):
            calls.append(url)
            return FakeResponse()

    provider = LLMProvider(
        id=uuid4(),
        user_id=None,
        name="builtin",
        type=ProviderType.OPENAI_COMPATIBLE,
        api_key="sk-test",
        base_url="http://gateway:9090/litellm_proxy/team/user-token/v1",
        model="",
        is_builtin=True,
        visible_to_all=True,
    )

    monkeypatch.setattr(provider_service.httpx, "Client", FakeClient)

    assert provider_service._fetch_builtin_provider_models_via_gateway(
        provider,
        "http://gateway:9090/litellm_proxy/team/user-token/v1",
        "user-token",
    ) == ["gateway-model-1"]
    assert provider_service._fetch_builtin_provider_models_via_gateway(
        provider,
        "http://gateway:9090/litellm_proxy/team/user-token/v1",
        "user-token",
    ) == ["gateway-model-2"]

    assert calls == [
        "http://gateway:9090/litellm_proxy/team/user-token/v1/models",
        "http://gateway:9090/litellm_proxy/team/user-token/v1/models",
    ]


def test_empty_gateway_model_result_is_not_cached(monkeypatch):
    calls = 0

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": []}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url, headers):
            nonlocal calls
            calls += 1
            return FakeResponse()

    provider = LLMProvider(
        id=uuid4(),
        user_id=None,
        name="builtin",
        type=ProviderType.OPENAI_COMPATIBLE,
        api_key="sk-test",
        base_url="http://gateway:9090/litellm_proxy/team/user-token/v1",
        model="",
        is_builtin=True,
        visible_to_all=True,
    )

    monkeypatch.setattr(provider_service.httpx, "Client", FakeClient)

    assert provider_service._fetch_builtin_provider_models_via_gateway(
        provider,
        "http://gateway:9090/litellm_proxy/team/user-token/v1",
        "user-token",
    ) == []
    assert provider_service._fetch_builtin_provider_models_via_gateway(
        provider,
        "http://gateway:9090/litellm_proxy/team/user-token/v1",
        "user-token",
    ) == []

    assert calls == 2


def test_gateway_model_discovery_filters_litellm_wildcard_and_embeddings():
    payload = {
        "data": [
            {"id": "jina-embeddings-v4", "object": "model"},
            {"id": "all-proxy-models", "object": "model"},
            {"id": "gpt-4o-mini", "object": "model"},
        ]
    }

    assert provider_service._extract_llm_model_ids(payload) == ["gpt-4o-mini"]


def test_format_connectivity_error_reports_builtin_quota_exhausted():
    message = provider_service._format_connectivity_error(
        RuntimeError("Model request failed: status=402. Payment Required")
    )

    assert "内置模型免费额度已用尽" in message
    assert "Builtin model free quota has been exhausted" in message


@pytest.mark.asyncio
async def test_stored_builtin_connectivity_rejects_model_outside_allowed_list(monkeypatch):
    provider_id = uuid4()
    user_id = uuid4()
    provider = LLMProvider(
        id=provider_id,
        user_id=None,
        name="Builtin LiteLLM",
        type=ProviderType.OPENAI_COMPATIBLE,
        api_key="sk-test",
        base_url="http://gateway:9090/litellm_proxy/team/{accessToken}/v1",
        model="deepseek-v4-flash;minimax_m25",
        is_builtin=True,
        visible_to_all=True,
    )

    class FakeDB:
        def get(self, _model, requested_provider_id):
            assert requested_provider_id == provider_id
            return provider

    async def fail_if_called(_provider_config, _prompt):
        raise AssertionError("connectivity test should not call LiteLLM for unauthorized models")

    monkeypatch.setattr(
        provider_service,
        "fetch_builtin_provider_models",
        lambda _provider, _access_token, user_id=None: ["minimax_m25"],
    )
    monkeypatch.setattr(provider_service, "_run_connectivity_test", fail_if_called)

    with pytest.raises(HTTPException) as exc_info:
        await provider_service.test_stored_provider_connectivity(
            FakeDB(),
            provider_id,
            SimpleNamespace(id=user_id, is_superuser=False),
            model="deepseek-v4-flash",
            access_token="user-token",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "所选模型不属于该供应商"
