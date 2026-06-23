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


def test_litellm_allowed_models_are_refetched_from_team(monkeypatch):
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_fetch_litellm_admin_json(path, params):
        calls.append((path, params))
        assert path == "/team/info"
        return {"team_info": {"models": [f"team-model-{len(calls)}"]}}

    monkeypatch.setattr(provider_service.settings, "LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setattr(provider_service.settings, "LITELLM_AUTH_TOKEN", "sk-test")
    monkeypatch.setattr(provider_service.settings, "TEAM_ID", "team-1")
    monkeypatch.setattr(provider_service, "_fetch_litellm_admin_json", fake_fetch_litellm_admin_json)

    assert provider_service._fetch_litellm_allowed_models("user-a") == ["team-model-1"]
    assert provider_service._fetch_litellm_allowed_models("user-a") == ["team-model-2"]
    assert provider_service._fetch_litellm_allowed_models("user-b") == ["team-model-3"]

    assert calls == [
        ("/team/info", {"team_id": "team-1"}),
        ("/team/info", {"team_id": "team-1"}),
        ("/team/info", {"team_id": "team-1"}),
    ]


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


def test_gateway_wildcard_expands_to_admin_model_info(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"id": "jina-embeddings-v4", "object": "model"},
                    {"id": "all-proxy-models", "object": "model"},
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url, headers):
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

    def fake_fetch_litellm_admin_json(path, params):
        assert path == "/model/info"
        assert params == {}
        return {
            "data": [
                {
                    "model_name": "jina-embeddings-v4",
                    "model_info": {"mode": "embedding"},
                },
                {
                    "model_name": "gpt-4o-mini",
                    "model_info": {"mode": "chat"},
                },
            ]
        }

    monkeypatch.setattr(provider_service.settings, "LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setattr(provider_service.settings, "LITELLM_AUTH_TOKEN", "sk-test")
    monkeypatch.setattr(provider_service.httpx, "Client", FakeClient)
    monkeypatch.setattr(provider_service, "_fetch_litellm_admin_json", fake_fetch_litellm_admin_json)

    assert provider_service._fetch_builtin_provider_models_via_gateway(
        provider,
        "http://gateway:9090/litellm_proxy/team/user-token/v1",
        "user-token",
    ) == ["gpt-4o-mini"]


def test_fetch_builtin_provider_models_provisions_via_gateway_before_admin(monkeypatch):
    provider = SimpleNamespace(
        id=uuid4(),
        base_url="http://gateway:9090/litellm_proxy/team/{accessToken}/v1",
        api_key="sk-test",
    )
    calls: list[str] = []

    def fake_fetch_via_gateway(_provider, base_url, access_token):
        calls.append("gateway")
        assert base_url == "http://gateway:9090/litellm_proxy/team/user-token/v1"
        assert access_token == "user-token"
        return ["team-model"]

    def fake_fetch_allowed_models(user_id):
        calls.append("admin")
        assert user_id == "user-a"
        return ["user-model"]

    monkeypatch.setattr(provider_service, "_fetch_builtin_provider_models_via_gateway", fake_fetch_via_gateway)
    monkeypatch.setattr(provider_service, "_fetch_litellm_allowed_models", fake_fetch_allowed_models)

    assert provider_service.fetch_builtin_provider_models(
        provider,
        access_token="user-token",
        user_id="user-a",
    ) == ["user-model"]
    assert calls == ["gateway", "admin"]


def test_litellm_allowed_models_expands_team_wildcard_without_access_token(monkeypatch):
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_fetch_litellm_admin_json(path, params):
        calls.append((path, params))
        if path == "/team/info":
            return {"team_info": {"models": ["all-proxy-models"]}}
        if path == "/model/info":
            return {
                "data": [
                    {
                        "model_name": "jina-embeddings-v4",
                        "model_info": {"mode": "embedding"},
                    },
                    {
                        "model_name": "gpt-4o-mini",
                        "model_info": {"mode": "chat"},
                    },
                ]
            }
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(provider_service.settings, "LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setattr(provider_service.settings, "LITELLM_AUTH_TOKEN", "sk-test")
    monkeypatch.setattr(provider_service.settings, "TEAM_ID", "team-1")
    monkeypatch.setattr(provider_service, "_fetch_litellm_admin_json", fake_fetch_litellm_admin_json)

    assert provider_service._fetch_litellm_allowed_models("user-a") == ["gpt-4o-mini"]
    assert calls == [
        ("/team/info", {"team_id": "team-1"}),
        ("/model/info", {}),
    ]


def test_litellm_allowed_models_use_configured_team_only(monkeypatch):
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_fetch_litellm_admin_json(path, params):
        calls.append((path, params))
        if path == "/team/info" and params == {"team_id": "team-1"}:
            return {"team_info": {"models": ["current-team-model"]}}
        if path == "/team/info" and params == {"team_id": "other-team"}:
            return {"team_info": {"models": ["other-team-model"]}}
        raise AssertionError(f"unexpected request {path} {params}")

    monkeypatch.setattr(provider_service.settings, "LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setattr(provider_service.settings, "LITELLM_AUTH_TOKEN", "sk-test")
    monkeypatch.setattr(provider_service.settings, "TEAM_ID", "team-1")
    monkeypatch.setattr(provider_service, "_fetch_litellm_admin_json", fake_fetch_litellm_admin_json)

    assert provider_service._fetch_litellm_allowed_models("user-a") == ["current-team-model"]
    assert calls == [
        ("/team/info", {"team_id": "team-1"}),
    ]


def test_litellm_allowed_models_do_not_query_user_models(monkeypatch):
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_fetch_litellm_admin_json(path, params):
        calls.append((path, params))
        if path == "/team/info":
            return {"team_info": {"models": ["gpt-4o"]}}
        raise AssertionError(f"unexpected request {path} {params}")

    monkeypatch.setattr(provider_service.settings, "LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setattr(provider_service.settings, "LITELLM_AUTH_TOKEN", "sk-test")
    monkeypatch.setattr(provider_service.settings, "TEAM_ID", "team-1")
    monkeypatch.setattr(provider_service, "_fetch_litellm_admin_json", fake_fetch_litellm_admin_json)

    assert provider_service._fetch_litellm_allowed_models("user-a") == ["gpt-4o"]
    assert calls == [
        ("/team/info", {"team_id": "team-1"}),
    ]


def test_litellm_allowed_models_ignore_nested_member_key_wildcards(monkeypatch):
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_fetch_litellm_admin_json(path, params):
        calls.append((path, params))
        if path == "/team/info":
            return {
                "team_info": {
                    "models": ["gpt-4o"],
                    "team_memberships": [
                        {
                            "user_id": "user-a",
                            "keys": [{"models": ["all-proxy-models"]}],
                        }
                    ],
                }
            }
        if path == "/model/info":
            return {"data": [{"model_name": "gpt-4o-mini", "model_info": {"mode": "chat"}}]}
        raise AssertionError(f"unexpected request {path} {params}")

    monkeypatch.setattr(provider_service.settings, "LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setattr(provider_service.settings, "LITELLM_AUTH_TOKEN", "sk-test")
    monkeypatch.setattr(provider_service.settings, "TEAM_ID", "team-1")
    monkeypatch.setattr(provider_service, "_fetch_litellm_admin_json", fake_fetch_litellm_admin_json)

    assert provider_service._fetch_litellm_allowed_models("user-a") == ["gpt-4o"]
    assert calls == [
        ("/team/info", {"team_id": "team-1"}),
    ]


def test_litellm_allowed_models_ignore_model_aliases_when_models_are_explicit(monkeypatch):
    def fake_fetch_litellm_admin_json(path, params):
        if path == "/team/info":
            return {
                "team_info": {
                    "models": ["gpt-4o"],
                    "model_aliases": {"gpt-4o-mini": "openai/gpt-4o-mini"},
                }
            }
        raise AssertionError(f"unexpected request {path} {params}")

    monkeypatch.setattr(provider_service.settings, "LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setattr(provider_service.settings, "LITELLM_AUTH_TOKEN", "sk-test")
    monkeypatch.setattr(provider_service.settings, "TEAM_ID", "team-1")
    monkeypatch.setattr(provider_service, "_fetch_litellm_admin_json", fake_fetch_litellm_admin_json)

    assert provider_service._fetch_litellm_allowed_models("user-a") == ["gpt-4o"]


def test_litellm_allowed_models_ignore_member_models_and_use_team_models(monkeypatch):
    def fake_fetch_litellm_admin_json(path, params):
        if path == "/team/info":
            return {
                "team_info": {
                    "models": ["gpt-4o"],
                    "team_memberships": [
                        {
                            "user_id": "user-a",
                            "allowed_models": ["gpt-4o", "gpt-4o-mini"],
                        }
                    ],
                }
            }
        raise AssertionError(f"unexpected request {path} {params}")

    monkeypatch.setattr(provider_service.settings, "LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setattr(provider_service.settings, "LITELLM_AUTH_TOKEN", "sk-test")
    monkeypatch.setattr(provider_service.settings, "TEAM_ID", "team-1")
    monkeypatch.setattr(provider_service, "_fetch_litellm_admin_json", fake_fetch_litellm_admin_json)

    assert provider_service._fetch_litellm_allowed_models("user-a") == ["gpt-4o"]


def test_litellm_allowed_models_empty_member_models_do_not_affect_team_models(monkeypatch):
    def fake_fetch_litellm_admin_json(path, params):
        if path == "/team/info":
            return {
                "team_info": {
                    "models": ["gpt-4o", "gpt-4o-mini"],
                    "team_memberships": [
                        {
                            "user_id": "user-a",
                            "allowed_models": [],
                        }
                    ],
                }
            }
        raise AssertionError(f"unexpected request {path} {params}")

    monkeypatch.setattr(provider_service.settings, "LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setattr(provider_service.settings, "LITELLM_AUTH_TOKEN", "sk-test")
    monkeypatch.setattr(provider_service.settings, "TEAM_ID", "team-1")
    monkeypatch.setattr(provider_service, "_fetch_litellm_admin_json", fake_fetch_litellm_admin_json)

    assert provider_service._fetch_litellm_allowed_models("user-a") == ["gpt-4o", "gpt-4o-mini"]


def test_litellm_allowed_models_team_wildcard_ignores_member_models(monkeypatch):
    def fake_fetch_litellm_admin_json(path, params):
        if path == "/team/info":
            return {
                "team_info": {
                    "models": ["all-proxy-models"],
                    "team_memberships": [
                        {
                            "user_id": "user-a",
                            "allowed_models": ["gpt-4o-mini"],
                        }
                    ],
                }
            }
        if path == "/model/info":
            return {
                "data": [
                    {"model_name": "gpt-4o", "model_info": {"mode": "chat"}},
                    {"model_name": "gpt-4o-mini", "model_info": {"mode": "chat"}},
                ]
            }
        raise AssertionError(f"unexpected request {path} {params}")

    monkeypatch.setattr(provider_service.settings, "LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setattr(provider_service.settings, "LITELLM_AUTH_TOKEN", "sk-test")
    monkeypatch.setattr(provider_service.settings, "TEAM_ID", "team-1")
    monkeypatch.setattr(provider_service, "_fetch_litellm_admin_json", fake_fetch_litellm_admin_json)

    assert provider_service._fetch_litellm_allowed_models("user-a") == ["gpt-4o", "gpt-4o-mini"]


def test_fetch_builtin_provider_models_prefers_authoritative_allowed_models(monkeypatch):
    provider = SimpleNamespace(
        id=uuid4(),
        base_url="http://gateway:9090/litellm_proxy/team/user-token/v1",
        api_key="sk-test",
    )

    monkeypatch.setattr(
        provider_service,
        "_fetch_litellm_allowed_models",
        lambda user_id: ["shared-model", "extra-model"],
    )
    monkeypatch.setattr(
        provider_service,
        "_fetch_builtin_provider_models_via_gateway",
        lambda *_args, **_kwargs: ["shared-model", "team-model"],
    )

    assert provider_service.fetch_builtin_provider_models(
        provider,
        access_token="user-token",
        user_id="user-a",
    ) == ["shared-model", "extra-model"]


def test_fetch_builtin_provider_models_returns_shared_models_without_user_extra(monkeypatch):
    provider = SimpleNamespace(
        id=uuid4(),
        base_url="http://gateway:9090/litellm_proxy/team/user-token/v1",
        api_key="sk-test",
    )

    monkeypatch.setattr(provider_service, "_fetch_litellm_allowed_models", lambda user_id: [])
    monkeypatch.setattr(
        provider_service,
        "_fetch_builtin_provider_models_via_gateway",
        lambda *_args, **_kwargs: ["team-model"],
    )

    assert provider_service.fetch_builtin_provider_models(
        provider,
        access_token="user-token",
        user_id="user-a",
    ) == ["team-model"]


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
