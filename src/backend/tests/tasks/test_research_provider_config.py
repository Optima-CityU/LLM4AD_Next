from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from app.core.security import decode_access_token
from app.tasks.research_runner import config_builder


def test_resolve_builtin_provider_for_arc_replaces_gateway_access_token(monkeypatch):
    user_id = uuid4()
    provider_id = uuid4()
    provider = SimpleNamespace(
        type=SimpleNamespace(value="openai_compatible"),
        model="test-model",
        base_url=(
            "http://gateway:9090/litellm_proxy/${TEAM_ID}/{accessToken}/v1"
        ),
        api_key="EMPTY",
        auth_token="",
        timeout=60,
        is_builtin=True,
    )

    class FakeDB:
        def get(self, _model, requested_id):
            assert requested_id == provider_id
            return provider

    @contextmanager
    def fake_session():
        yield FakeDB()

    monkeypatch.setattr(config_builder, "get_db_session", fake_session)
    monkeypatch.setattr("app.services.provider_service.settings.TEAM_ID", "team-123")

    resolved = config_builder.resolve_provider_for_arc(
        str(provider_id), "test-model", user_id=user_id
    )

    prefix = "http://gateway:9090/litellm_proxy/team-123/"
    assert resolved["base_url"].startswith(prefix)
    assert "{accessToken}" not in resolved["base_url"]
    token = resolved["base_url"][len(prefix) : -len("/v1")]
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == str(user_id)
