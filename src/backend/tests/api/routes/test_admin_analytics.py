from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api import deps
from app.core.config import settings
from app.main import app
from app.services import admin_analytics_service


def test_admin_can_read_analytics_overview(
    client: TestClient,
    monkeypatch,
) -> None:
    calls: list[tuple[object, str, str]] = []

    def fake_overview(db, access_token: str, date_range: str):
        calls.append((db, access_token, date_range))
        return {
            "range": date_range,
            "users": {"total": 1},
            "github": {"available": True, "stars": 10},
            "plausible": {"available": False, "message": "Plausible Stats API key is not configured."},
        }

    app.dependency_overrides[deps.get_current_active_superuser] = lambda: SimpleNamespace(is_superuser=True)
    app.dependency_overrides[deps.get_db] = lambda: object()
    monkeypatch.setattr(admin_analytics_service, "build_analytics_overview", fake_overview)
    try:
        response = client.get(
            f"{settings.API_V1_STR}/admin/analytics/overview?range=7d",
            headers={"Authorization": "Bearer admin-token"},
        )
    finally:
        app.dependency_overrides.pop(deps.get_current_active_superuser, None)
        app.dependency_overrides.pop(deps.get_db, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["range"] == "7d"
    assert payload["github"]["stars"] == 10
    assert calls[0][1:] == ("admin-token", "7d")


def test_normal_user_cannot_read_analytics_overview(
    client: TestClient,
) -> None:
    app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(is_superuser=False)
    app.dependency_overrides[deps.get_db] = lambda: object()
    try:
        response = client.get(
            f"{settings.API_V1_STR}/admin/analytics/overview",
            headers={"Authorization": "Bearer normal-user-token"},
        )
    finally:
        app.dependency_overrides.pop(deps.get_current_user, None)
        app.dependency_overrides.pop(deps.get_db, None)

    assert response.status_code == 403
