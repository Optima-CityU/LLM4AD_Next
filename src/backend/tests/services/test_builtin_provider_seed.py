from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.config import settings
from app.utils import init_db


def test_builtin_provider_timeout_defaults_to_600_seconds():
    assert getattr(settings, "BUILTIN_PROVIDER_TIMEOUT", None) == 600.0


def test_seed_builtin_provider_updates_existing_timeout(monkeypatch):
    provider = SimpleNamespace(
        id="builtin-id",
        name="builtin-trial",
        type="openai_compatible",
        base_url="http://gateway:9090/litellm_proxy/team/token/v1",
        api_key="EMPTY",
        auth_token="",
        model="test-model",
        max_tokens=16384,
        timeout=60.0,
        updated_time=None,
    )
    fake_session = MagicMock()
    fake_session.__enter__.return_value = fake_session
    fake_session.exec.return_value.all.return_value = [provider]

    monkeypatch.setattr(init_db, "Session", lambda _engine: fake_session)
    monkeypatch.setattr(settings, "BUILTIN_PROVIDER_NAME", provider.name)
    monkeypatch.setattr(settings, "BUILTIN_PROVIDER_BASE_URL", provider.base_url)
    monkeypatch.setattr(settings, "BUILTIN_PROVIDER_API_KEY", provider.api_key)
    monkeypatch.setattr(settings, "BUILTIN_PROVIDER_MODELS", provider.model)
    monkeypatch.setattr(settings, "BUILTIN_PROVIDER_MAX_TOKENS", provider.max_tokens)
    monkeypatch.setattr(settings, "BUILTIN_PROVIDER_TIMEOUT", 600.0)

    resolved = init_db.seed_builtin_provider()

    assert resolved is provider
    assert provider.timeout == 600.0
