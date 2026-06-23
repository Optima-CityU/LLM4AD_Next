from types import SimpleNamespace
from uuid import UUID, uuid4

from app.services import user_default_model_service


class _FakeResult:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value


class _FakeSession:
    def __init__(self, builtin):
        self.builtin = builtin

    def exec(self, _stmt):
        return _FakeResult(self.builtin)


def test_default_slots_use_current_builtin_concrete_model(monkeypatch):
    builtin_id = uuid4()
    builtin = SimpleNamespace(id=builtin_id)

    def fake_fetch_builtin_provider_models(provider, access_token, user_id):
        assert provider is builtin
        assert access_token is None
        assert user_id == "00000000-0000-0000-0000-000000000001"
        return ["gpt-4o-mini"]

    monkeypatch.setattr(
        "app.services.provider_service.fetch_builtin_provider_models",
        fake_fetch_builtin_provider_models,
    )
    monkeypatch.setattr(user_default_model_service.settings, "BUILTIN_PROVIDER_DEFAULT_MODEL", "")

    result = user_default_model_service._build_default_slot_kwargs(
        _FakeSession(builtin),
        UUID(int=1),
    )

    assert result == {
        "planner_provider_id": builtin_id,
        "planner_model_name": "gpt-4o-mini",
        "coder_provider_id": builtin_id,
        "coder_model_name": "gpt-4o-mini",
        "report_provider_id": builtin_id,
        "report_model_name": "gpt-4o-mini",
        "other_provider_id": builtin_id,
        "other_model_name": "gpt-4o-mini",
    }


def test_default_slots_replace_stale_builtin_model(monkeypatch):
    user_id = UUID(int=1)
    builtin_id = uuid4()
    builtin = SimpleNamespace(id=builtin_id)
    config = SimpleNamespace(
        user_id=user_id,
        planner_provider_id=builtin_id,
        planner_model_name="deepseek-v4-flash",
        coder_provider_id=builtin_id,
        coder_model_name="deepseek-v4-flash",
        report_provider_id=builtin_id,
        report_model_name="deepseek-v4-flash",
        other_provider_id=builtin_id,
        other_model_name="deepseek-v4-flash",
    )

    monkeypatch.setattr(
        "app.services.provider_service.fetch_builtin_provider_models",
        lambda provider, access_token, user_id: ["minimax_m25"],
    )
    monkeypatch.setattr(user_default_model_service.settings, "BUILTIN_PROVIDER_DEFAULT_MODEL", "")

    changed = user_default_model_service._fill_missing_default_slots(
        _FakeSession(builtin),
        config,
        access_token="user-token",
    )

    assert changed is True
    assert config.planner_model_name == "minimax_m25"
    assert config.coder_model_name == "minimax_m25"
    assert config.report_model_name == "minimax_m25"
    assert config.other_model_name == "minimax_m25"
