import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.internal import gateway_users


class _Session:
    def __init__(self, user: object | None) -> None:
        self.user = user

    def get(self, _model: object, _user_id: uuid.UUID) -> object | None:
        return self.user


def test_gateway_user_lookup_returns_active_user_with_valid_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway_users.settings, "GATEWAY_BACKEND_SERVICE_TOKEN", "gateway-test-token", raising=False)
    user_id = uuid.uuid4()
    user = SimpleNamespace(id=user_id, email="memory@example.com", is_active=True)

    result = gateway_users.get_gateway_user(
        user_id=user_id,
        session=_Session(user),
        x_gateway_service_token="gateway-test-token",
    )

    assert result.model_dump() == {"id": str(user_id), "email": "memory@example.com", "is_active": True}


def test_gateway_user_lookup_rejects_invalid_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway_users.settings, "GATEWAY_BACKEND_SERVICE_TOKEN", "gateway-test-token", raising=False)

    with pytest.raises(HTTPException, match="Invalid gateway service token") as exc_info:
        gateway_users.get_gateway_user(
            user_id=uuid.uuid4(),
            session=_Session(None),
            x_gateway_service_token="wrong-token",
        )

    assert exc_info.value.status_code == 401


def test_gateway_user_lookup_rejects_inactive_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway_users.settings, "GATEWAY_BACKEND_SERVICE_TOKEN", "gateway-test-token", raising=False)
    inactive_user = SimpleNamespace(id=uuid.uuid4(), email="inactive@example.com", is_active=False)

    with pytest.raises(HTTPException, match="User is unavailable") as exc_info:
        gateway_users.get_gateway_user(
            user_id=inactive_user.id,
            session=_Session(inactive_user),
            x_gateway_service_token="gateway-test-token",
        )

    assert exc_info.value.status_code == 401
