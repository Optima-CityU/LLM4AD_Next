from types import SimpleNamespace

import httpx

from app.services import provider_service


def test_fetch_litellm_user_quotas_via_gateway_forwards_bearer(monkeypatch):
    calls: list[tuple[str, dict[str, str]]] = []
    user_id = "7f10275c-87e1-4899-8068-dd45a7edf77d"

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 200,
                "message": "success",
                "data": [
                    {
                        "user_id": user_id,
                        "user_email": "user@example.com",
                        "spend": 2.5,
                        "budget": 10,
                        "remaining": 7.5,
                    }
                ],
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url, headers):
            calls.append((url, headers))
            return FakeResponse()

    class FakeResult:
        def all(self):
            return [
                SimpleNamespace(
                    id=user_id,
                    email="user@example.com",
                    full_name="User Full Name",
                )
            ]

    class FakeDb:
        def exec(self, _query):
            return FakeResult()

    monkeypatch.setattr(provider_service.settings, "LITELLM_GATEWAY_BASE_URL", "http://gateway:9090")
    monkeypatch.setattr(provider_service.httpx, "Client", FakeClient)

    result = provider_service.fetch_litellm_user_quotas_via_gateway(FakeDb(), "admin-token")

    assert result["available"] is True
    assert result["items"][0]["user_id"] == user_id
    assert result["items"][0]["full_name"] == "User Full Name"
    assert calls == [
        (
            "http://gateway:9090/internal/litellm/users/quotas",
            {"Authorization": "Bearer admin-token"},
        )
    ]


def test_fetch_litellm_user_quotas_via_gateway_returns_error_on_failure(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url, headers):
            raise httpx.ConnectError("gateway unavailable")

    monkeypatch.setattr(provider_service.settings, "LITELLM_GATEWAY_BASE_URL", "http://gateway:9090")
    monkeypatch.setattr(provider_service.httpx, "Client", FakeClient)

    result = provider_service.fetch_litellm_user_quotas_via_gateway(object(), "admin-token")

    assert result["available"] is False
    assert result["items"] == []
    assert "gateway unavailable" in result["message"]


def test_fetch_current_litellm_quota_via_gateway_forwards_bearer(monkeypatch):
    calls: list[tuple[str, dict[str, str]]] = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "code": 200,
                "message": "success",
                "data": {
                    "user_id": "user-1",
                    "spend": 2.5,
                    "budget": 10,
                    "remaining": 7.5,
                },
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url, headers):
            calls.append((url, headers))
            return FakeResponse()

    monkeypatch.setattr(provider_service.settings, "LITELLM_GATEWAY_BASE_URL", "http://gateway:9090")
    monkeypatch.setattr(provider_service.httpx, "Client", FakeClient)

    result = provider_service._fetch_current_litellm_quota_via_gateway("user-token")

    assert result == {
        "user_id": "user-1",
        "spend": 2.5,
        "budget": 10,
        "remaining": 7.5,
    }
    assert calls == [
        (
            "http://gateway:9090/internal/litellm/users/me/quota",
            {"Authorization": "Bearer user-token"},
        )
    ]
