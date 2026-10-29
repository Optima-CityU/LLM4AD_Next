"""Local Codex ownership, provider binding, and experiment integration tests."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app import models
from app.schemas.provider import ProviderResponse
from app.services import local_codex_service as service


@pytest.fixture(autouse=True)
def bridge_settings(monkeypatch):
    """Use synthetic credentials and a fake local bridge."""
    monkeypatch.setattr(service.settings, "LOCAL_CODEX_BRIDGE_URL", "http://bridge.test:18143")
    monkeypatch.setattr(service.settings, "LOCAL_CODEX_BRIDGE_TOKEN", "test-bridge-secret" * 3)
    monkeypatch.setattr(service.settings, "ENVIRONMENT", "local")


@pytest.mark.asyncio
@pytest.mark.parametrize(("environment", "superuser"), [("production", True), ("local", False)])
async def test_only_local_owner_can_bind(monkeypatch, environment, superuser):
    """Remote deployments and other users must not share the host login."""
    monkeypatch.setattr(service.settings, "ENVIRONMENT", environment)
    db = Mock()
    user = SimpleNamespace(id=uuid.uuid4(), is_superuser=superuser)
    status = await service.get_status(db, user)
    assert not status["enabled"]
    with pytest.raises(HTTPException) as exc:
        await service.bind(db, user, "Codex", "", True)
    assert exc.value.status_code == 403
    db.exec.assert_not_called()


@pytest.mark.asyncio
async def test_binding_is_reused_and_sets_both_defaults(monkeypatch):
    """Repeated binding updates one provider; response masks its bridge credential."""
    user = SimpleNamespace(id=uuid.uuid4(), is_superuser=True)
    provider = models.LLMProvider(user_id=user.id, name="Old", model="old")
    state = {
        "enabled": True,
        "connected": True,
        "provider_id": str(provider.id),
        "models": [{"id": "model-a", "name": "Model A"}, {"id": "model-b", "name": "Model B"}],
    }
    monkeypatch.setattr(service, "get_status", AsyncMock(return_value=state))
    update = Mock()
    monkeypatch.setattr(service, "update_user_default_model", update)
    db = Mock()
    db.get.return_value = provider
    result = await service.bind(db, user, "本机 Codex CLI", "", True)
    assert result is provider
    assert result.type == models.ProviderType.OPENAI_COMPATIBLE
    assert result.model.split(";") == ["codex-default", "old", "model-a", "model-b"]
    assert result.timeout == 600 and result.max_retries == 0
    changes = update.call_args.args[2]
    assert changes["planner_provider_id"] == changes["coder_provider_id"] == provider.id
    assert changes["planner_model_name"] == changes["coder_model_name"] == "codex-default"
    data = ProviderResponse.model_validate(result).model_dump()
    assert data["is_local_codex"]
    assert data["api_key"] == "sk-***"
    assert service.settings.LOCAL_CODEX_BRIDGE_TOKEN not in str(data)
    await service.bind(db, user, "Codex", "custom-model", False)
    assert provider.model.split(";") == [
        "custom-model",
        "codex-default",
        "old",
        "model-a",
        "model-b",
    ]
    assert update.call_count == 1
    await service.bind(db, user, "Codex", "model-b", True)
    assert provider.model.split(";")[0] == "model-b"
    changes = update.call_args.args[2]
    assert changes["planner_model_name"] == changes["coder_model_name"] == "model-b"


@pytest.mark.asyncio
async def test_new_binding_stores_discovered_models_without_api_defaults(monkeypatch):
    """The ORM's gpt-4 default must not become a selectable local Codex model."""
    monkeypatch.setattr(
        service,
        "get_status",
        AsyncMock(
            return_value={
                "enabled": True,
                "connected": True,
                "provider_id": None,
                "models": [{"id": "model-a", "name": "Model A"}],
            }
        ),
    )
    result = await service.bind(Mock(), SimpleNamespace(id=uuid.uuid4()), "Codex", "model-a", False)
    assert result.model == "model-a;codex-default"


@pytest.mark.asyncio
async def test_not_logged_in_cannot_create_provider(monkeypatch):
    """A failed connection leaves provider records unchanged."""
    monkeypatch.setattr(
        service, "get_status", AsyncMock(return_value={"enabled": True, "connected": False})
    )
    db = Mock()
    with pytest.raises(HTTPException) as exc:
        await service.bind(db, SimpleNamespace(), "Codex", "", True)
    assert exc.value.status_code == 409
    db.add.assert_not_called()


def test_codex_experiment_selects_plain_text_coder_and_preserves_api_configs():
    """CLI-generated code uses the existing custom coder inside the runner."""
    args = {
        "planner": {"provider": "local"},
        "coder": {"provider": "local", "type": "claude_code", "timeout": 30},
    }
    service.configure_experiment(args, {"local": {"base_url": service.bridge_base_url()}})
    assert args["coder"]["type"] == "custom"
    assert args["coder"]["timeout"] == 600
    ordinary = {"coder": {"provider": "api", "type": "claude_code"}}
    service.configure_experiment(ordinary, {"api": {"base_url": "https://api.example.test/v1"}})
    assert ordinary["coder"]["type"] == "claude_code"
    args["multimodal"] = {"enabled": True}
    with pytest.raises(HTTPException) as exc:
        service.configure_experiment(args, {"local": {"base_url": service.bridge_base_url()}})
    assert exc.value.status_code == 400


@pytest.mark.parametrize("selected_model", ["codex-default", "model-a", "model-b"])
def test_resolve_codex_defaults_without_an_unrelated_api_provider(monkeypatch, selected_model):
    """A new local-only account can run Python evaluations without other API defaults."""
    monkeypatch.setattr("boto3.client", Mock(return_value=Mock()))
    from app.services.task_service.execution import _resolve_providers

    user = SimpleNamespace(id=uuid.uuid4(), is_superuser=True)
    provider = models.LLMProvider(
        user_id=user.id,
        name="Codex",
        model="model-a;codex-default;model-b",
        base_url=service.bridge_base_url(),
    )
    defaults = SimpleNamespace(
        planner_provider_id=provider.id,
        planner_model_name=selected_model,
        coder_provider_id=provider.id,
        coder_model_name=selected_model,
        other_provider_id=None,
        other_model_name=None,
        embedding_enabled=False,
    )
    monkeypatch.setattr(
        "app.services.user_default_model_service.get_user_default_model", lambda *args: defaults
    )
    db = Mock()
    db.exec.return_value.all.return_value = [provider]
    result = _resolve_providers(db, {"coder": {"type": "claude_code"}}, user)
    assert (
        result["planner"]["provider"]
        == result["coder"]["provider"]
        == result["evaluator"]["provider"]
    )
    assert result["coder"]["type"] == "custom"
    assert len(result["providers"]) == 1
    assert result["providers"][0]["model"] == selected_model
