"""Tests for MindMemOS memory connectivity checks."""

import pytest

from app.schemas.memory import MemoryTestRequest
from app.services import memory_service


class FakeResponse:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeAsyncClient:
    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None, **kwargs):
        self.response = response or FakeResponse()
        self.error = error
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get(self, url: str):
        if self.error is not None:
            raise self.error
        self.url = url
        return self.response


@pytest.mark.asyncio
async def test_mindmemos_test_requires_connection_fields():
    response = await memory_service.test_memory_connectivity(
        MemoryTestRequest(
            type="mindmemos_cloud",
            mindmemos_base_url="",
            mindmemos_api_key="",
            mindmemos_user_id="",
        ),
        http_client_factory=FakeAsyncClient,
    )

    assert response.ok is False
    assert "mindmemos_base_url" in response.message


@pytest.mark.asyncio
async def test_mindmemos_test_checks_healthz():
    response = await memory_service.test_memory_connectivity(
        MemoryTestRequest(
            type="mindmemos_cloud",
            mindmemos_base_url="http://mindmemos-api:8000",
            mindmemos_api_key="sk-test",
            mindmemos_user_id="user-1",
        ),
        http_client_factory=FakeAsyncClient,
    )

    assert response.ok is True
    assert response.backend_type == "mindmemos_cloud"
    assert response.base_url == "http://mindmemos-api:8000"


@pytest.mark.asyncio
async def test_mindmemos_test_reports_health_failure():
    def factory(**kwargs):
        return FakeAsyncClient(error=RuntimeError("connection refused"), **kwargs)

    response = await memory_service.test_memory_connectivity(
        MemoryTestRequest(
            type="mindmemos_cloud",
            mindmemos_base_url="http://mindmemos-api:8000",
            mindmemos_api_key="sk-test",
            mindmemos_user_id="user-1",
        ),
        http_client_factory=factory,
    )

    assert response.ok is False
    assert "connection refused" in response.message
