from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models import ProviderType
from app.services import provider_service


@pytest.mark.asyncio
async def test_stored_provider_test_uses_new_model_from_owned_provider_form(
    monkeypatch,
):
    provider = SimpleNamespace(
        type=ProviderType.OPENAI_COMPATIBLE,
        api_key="provider-key",
        auth_token="",
        base_url="https://provider.example/v1",
        model="gpt-4o",
        is_builtin=False,
    )
    captured: dict[str, str] = {}

    async def fake_connectivity_test(config: dict, _prompt: str):
        captured.update(config)
        return provider_service.ProviderTestResponse(success=True, message="connected")

    monkeypatch.setattr(provider_service, "_run_connectivity_test", fake_connectivity_test)
    monkeypatch.setattr(
        provider_service,
        "get_provider_with_auth",
        lambda *_args: provider,
    )

    response = await provider_service.test_stored_provider_connectivity(
        None,
        None,
        SimpleNamespace(),
        model="newly-added-model",
    )

    assert response.success is True
    assert captured["model"] == "newly-added-model"
    assert captured["api_key"] == "provider-key"


@pytest.mark.asyncio
async def test_stored_builtin_provider_rejects_unregistered_model(monkeypatch):
    provider = SimpleNamespace(
        type=ProviderType.OPENAI_COMPATIBLE,
        api_key="gateway-key",
        auth_token="",
        base_url="https://gateway.example/v1",
        model="registered-model",
        is_builtin=True,
    )
    monkeypatch.setattr(
        provider_service,
        "get_provider_with_auth",
        lambda *_args: provider,
    )
    monkeypatch.setattr(
        provider_service,
        "fetch_builtin_provider_models",
        lambda *_args, **_kwargs: ["registered-model"],
    )

    with pytest.raises(HTTPException, match="所选模型不属于该供应商"):
        await provider_service.test_stored_provider_connectivity(
            None,
            None,
            SimpleNamespace(id="user-1"),
            model="unregistered-model",
        )
