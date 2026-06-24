from fastapi.testclient import TestClient

from app.api import deps
from app.core.config import settings
from app.main import app
from app.services import provider_service


def test_admin_can_read_litellm_user_quotas(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    monkeypatch,
) -> None:
    captured_tokens: list[str] = []

    def fake_fetch(_db, token: str):
        captured_tokens.append(token)
        return {
            "available": True,
            "items": [
                {
                    "user_id": "user-1",
                    "user_email": "user@example.com",
                    "spend": 2.5,
                    "budget": 10,
                    "remaining": 7.5,
                }
            ],
            "total": 1,
            "message": "success",
        }

    monkeypatch.setattr(provider_service, "fetch_litellm_user_quotas_via_gateway", fake_fetch)

    response = client.get(
        f"{settings.API_V1_STR}/llm4ad/providers/admin/litellm-user-quotas",
        headers=superuser_token_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["items"][0]["user_id"] == "user-1"
    assert captured_tokens == [superuser_token_headers["Authorization"].removeprefix("Bearer ")]


def test_normal_user_cannot_read_litellm_user_quotas(
    client: TestClient,
) -> None:
    app.dependency_overrides[deps.get_current_user] = lambda: type(
        "User",
        (),
        {"is_superuser": False},
    )()
    try:
        response = client.get(
            f"{settings.API_V1_STR}/llm4ad/providers/admin/litellm-user-quotas",
            headers={"Authorization": "Bearer normal-user-token"},
        )
    finally:
        app.dependency_overrides.pop(deps.get_current_user, None)

    assert response.status_code == 403
