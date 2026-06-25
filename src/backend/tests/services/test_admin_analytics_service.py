from datetime import UTC, datetime
from uuid import UUID

from app.services import admin_analytics_service


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def one(self):
        return self.value


class _RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _FakeDb:
    def __init__(self):
        self.calls = 0

    def exec(self, _query):
        self.calls += 1
        values = [
            _ScalarResult(3),  # users total
            _ScalarResult(2),  # users active
            _ScalarResult(1),  # users superusers
            _ScalarResult(2),  # users verified
            _ScalarResult(2),  # projects total
            _ScalarResult(4),  # tasks total
            _RowsResult([("completed", 3), ("failed", 1)]),
            _RowsResult([("2026-06-25", 2)]),
            _RowsResult([("User A", "a@example.com", 3, 2)]),
            _ScalarResult(5),  # feedback total
            _RowsResult([("pending", 4), ("resolved", 1)]),
            _RowsResult([("bug", 5)]),
            _RowsResult([("high", 2), ("medium", 3)]),
            _RowsResult(
                [
                    (
                        UUID("00000000-0000-0000-0000-000000000001"),
                        "Crash on dashboard",
                        "Dashboard crashes after opening analytics.",
                        "bug",
                        "pending",
                        "high",
                        datetime(2026, 6, 25, tzinfo=UTC),
                        "contact@example.com",
                        "https://example.com/analytics",
                        "Safari 18",
                        "We are checking it.",
                        "dashboard,crash",
                        "User A",
                        "a@example.com",
                    )
                ]
            ),
            _ScalarResult(3),  # providers total
            _ScalarResult(1),  # builtin providers
        ]
        return values[self.calls - 1]


def test_build_analytics_overview_combines_local_and_external_sections(monkeypatch):
    monkeypatch.setattr(
        admin_analytics_service,
        "fetch_github_summary",
        lambda: {"available": True, "stars": 12, "recent_issues": []},
    )
    monkeypatch.setattr(
        admin_analytics_service,
        "fetch_plausible_summary",
        lambda date_range: {
            "available": False,
            "date_range": date_range,
        },
    )
    monkeypatch.setattr(
        admin_analytics_service,
        "fetch_litellm_quota_summary",
        lambda _db, _token: {"available": True, "total_spend": 2.5},
    )

    overview = admin_analytics_service.build_analytics_overview(
        _FakeDb(),
        access_token="admin-token",
        date_range="7d",
    )

    assert overview["range"] == "7d"
    assert overview["users"] == {
        "total": 3,
        "active": 2,
        "inactive": 1,
        "superusers": 1,
        "email_verified": 2,
    }
    assert overview["projects"]["total"] == 2
    assert overview["tasks"]["total"] == 4
    assert overview["tasks"]["by_status"] == {
        "uninitialized": 0,
        "pending": 0,
        "running": 0,
        "completed": 3,
        "failed": 1,
    }
    assert overview["feedback"]["pending"] == 4
    assert overview["feedback"]["recent_items"][0]["title"] == "Crash on dashboard"
    assert overview["feedback"]["recent_items"][0]["id"] == "00000000-0000-0000-0000-000000000001"
    assert overview["feedback"]["recent_items"][0]["content"] == "Dashboard crashes after opening analytics."
    assert overview["feedback"]["recent_items"][0]["page_url"] == "https://example.com/analytics"
    assert overview["feedback"]["recent_items"][0]["browser_info"] == "Safari 18"
    assert overview["github"]["stars"] == 12
    assert overview["litellm"]["total_spend"] == 2.5


def test_fetch_plausible_summary_requires_site_id(monkeypatch):
    monkeypatch.setattr(admin_analytics_service.settings, "PLAUSIBLE_API_BASE_URL", "https://plausible.example")
    monkeypatch.setattr(admin_analytics_service.settings, "PLAUSIBLE_SITE_ID", "")
    monkeypatch.setattr(admin_analytics_service.settings, "PLAUSIBLE_API_KEY", "")

    summary = admin_analytics_service.fetch_plausible_summary("30d")

    assert summary["available"] is False
    assert summary["api_base_url"] == "https://plausible.example"
    assert summary["site_id"] is None
    assert "site_id is not configured" in summary["message"]


def test_fetch_plausible_summary_uses_official_api_configuration(monkeypatch):
    calls: list[tuple[str, dict, dict]] = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def post(self, url, headers, json):
            calls.append((url, headers, json))
            dimensions = json.get("dimensions") or []
            if dimensions == ["time:day"]:
                return FakeResponse(
                    {"results": [{"dimensions": ["2026-06-25"], "metrics": [3, 9]}]}
                )
            if dimensions:
                return FakeResponse(
                    {"results": [{"dimensions": [f"{dimensions[0]} value"], "metrics": [7]}]}
                )
            return FakeResponse({"results": [{"metrics": [5, 4, 12, 3.0, 20.0, 42]}]})

    monkeypatch.setattr(admin_analytics_service.settings, "PLAUSIBLE_API_BASE_URL", "https://plausible.example")
    monkeypatch.setattr(admin_analytics_service.settings, "PLAUSIBLE_SITE_ID", "site.example")
    monkeypatch.setattr(admin_analytics_service.settings, "PLAUSIBLE_API_KEY", "stats-key")
    monkeypatch.setattr(admin_analytics_service.httpx, "Client", FakeClient)

    summary = admin_analytics_service.fetch_plausible_summary("30d")

    assert summary["available"] is True
    assert summary["site_id"] == "site.example"
    assert summary["api_base_url"] == "https://plausible.example"
    assert summary["metrics"]["visitors"] == 5
    assert summary["metrics"]["visit_duration"] == 42
    assert summary["trend"] == [{"date": "2026-06-25", "visitors": 3, "pageviews": 9}]
    assert summary["countries"] == [{"name": "visit:country_name value", "value": 7}]
    assert summary["cities"] == [{"name": "visit:city_name value", "value": 7}]
    assert summary["devices"] == [{"name": "visit:device value", "value": 7}]
    assert summary["browsers"] == [{"name": "visit:browser value", "value": 7}]
    assert summary["operating_systems"] == [{"name": "visit:os value", "value": 7}]
    assert calls[0][0] == "https://plausible.example/api/v2/query"
    assert calls[0][1] == {"Authorization": "Bearer stats-key"}
    assert all(call[2]["site_id"] == "site.example" for call in calls)
    breakdown_calls = [call for call in calls if call[2].get("dimensions") and call[2]["dimensions"] != ["time:day"]]
    assert all("limit" not in call[2] for call in breakdown_calls)
    assert all(call[2]["pagination"] == {"limit": 8, "offset": 0} for call in breakdown_calls)
