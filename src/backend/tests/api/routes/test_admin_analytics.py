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


def test_admin_can_read_modular_analytics_sections(
    client: TestClient,
    monkeypatch,
) -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    def fake_summary(db):
        calls.append(("summary", (db,)))
        return {"users": {"total": 2}, "projects": {"total": 1}}

    def fake_tasks(db):
        calls.append(("tasks", (db,)))
        return {"total": 3, "by_status": {"running": 1}, "trend": [], "top_users": []}

    def fake_feedback(db):
        calls.append(("feedback", (db,)))
        return {"total": 4, "recent_items": []}

    def fake_litellm(db, access_token: str):
        calls.append(("litellm", (db, access_token)))
        return {"available": True, "total_spend": 5}

    def fake_plausible(date_range: str):
        calls.append(("plausible", (date_range,)))
        return {"available": True, "date_range": date_range}

    def fake_github():
        calls.append(("github", ()))
        return {"available": True, "stars": 6}

    app.dependency_overrides[deps.get_current_active_superuser] = lambda: SimpleNamespace(is_superuser=True)
    app.dependency_overrides[deps.get_db] = lambda: object()
    monkeypatch.setattr(admin_analytics_service, "build_operations_summary", fake_summary)
    monkeypatch.setattr(admin_analytics_service, "build_operations_tasks", fake_tasks)
    monkeypatch.setattr(admin_analytics_service, "build_operations_feedback", fake_feedback)
    monkeypatch.setattr(admin_analytics_service, "build_operations_litellm", fake_litellm)
    monkeypatch.setattr(admin_analytics_service, "build_visitors_plausible", fake_plausible)
    monkeypatch.setattr(admin_analytics_service, "build_visitors_github", fake_github)

    try:
        responses = {
            "summary": client.get(
                f"{settings.API_V1_STR}/admin/analytics/operations/summary",
                headers={"Authorization": "Bearer admin-token"},
            ),
            "tasks": client.get(
                f"{settings.API_V1_STR}/admin/analytics/operations/tasks",
                headers={"Authorization": "Bearer admin-token"},
            ),
            "feedback": client.get(
                f"{settings.API_V1_STR}/admin/analytics/operations/feedback",
                headers={"Authorization": "Bearer admin-token"},
            ),
            "litellm": client.get(
                f"{settings.API_V1_STR}/admin/analytics/operations/litellm",
                headers={"Authorization": "Bearer admin-token"},
            ),
            "plausible": client.get(
                f"{settings.API_V1_STR}/admin/analytics/visitors/plausible?range=7d",
                headers={"Authorization": "Bearer admin-token"},
            ),
            "github": client.get(
                f"{settings.API_V1_STR}/admin/analytics/visitors/github",
                headers={"Authorization": "Bearer admin-token"},
            ),
        }
    finally:
        app.dependency_overrides.pop(deps.get_current_active_superuser, None)
        app.dependency_overrides.pop(deps.get_db, None)

    assert all(response.status_code == 200 for response in responses.values())
    assert responses["summary"].json()["users"]["total"] == 2
    assert responses["tasks"].json()["by_status"]["running"] == 1
    assert responses["feedback"].json()["total"] == 4
    assert responses["litellm"].json()["total_spend"] == 5
    assert responses["plausible"].json()["date_range"] == "7d"
    assert responses["github"].json()["stars"] == 6
    assert ("litellm", (calls[3][1][0], "admin-token")) in calls
    assert ("plausible", ("7d",)) in calls


def test_normal_user_cannot_read_modular_analytics_sections(client: TestClient) -> None:
    app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(is_superuser=False)
    app.dependency_overrides[deps.get_db] = lambda: object()
    try:
        response = client.get(
            f"{settings.API_V1_STR}/admin/analytics/operations/summary",
            headers={"Authorization": "Bearer normal-user-token"},
        )
    finally:
        app.dependency_overrides.pop(deps.get_current_user, None)
        app.dependency_overrides.pop(deps.get_db, None)

    assert response.status_code == 403
