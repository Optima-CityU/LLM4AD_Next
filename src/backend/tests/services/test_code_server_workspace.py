from datetime import UTC, datetime

import docker
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services import code_server_service


class _FakeNetworkCollection:
    def get(self, name: str):
        return object()


class _FakeContainer:
    id = "fake-container-id"
    name = "fake-container"
    status = "created"
    attrs = {"State": {"StartedAt": "2026-06-27T10:00:00.000000000Z"}}

    def reload(self):
        return None


class _FakeContainerCollection:
    def __init__(self):
        self.volumes = None

    def get(self, name: str):
        raise docker.errors.NotFound("missing")

    def run(self, *args, **kwargs):
        self.volumes = kwargs["volumes"]
        return _FakeContainer()


class _FakeDockerClient:
    def __init__(self):
        self.networks = _FakeNetworkCollection()
        self.containers = _FakeContainerCollection()


def test_code_server_mount_paths_join_host_project_home_without_trailing_slash(monkeypatch):
    client = _FakeDockerClient()
    monkeypatch.setattr(code_server_service, "get_docker_client", lambda: client)
    monkeypatch.setattr(settings, "HOST_PROJECT_HOME", "/srv/llm4ad/app-data")

    container, started_at = code_server_service.get_or_start_container("code_user-123")

    assert container.id == "fake-container-id"
    assert started_at == datetime(2026, 6, 27, 10, 0, tzinfo=UTC)
    assert "/srv/llm4ad/app-data/code_user-123" in client.containers.volumes
    assert "/srv/llm4ad/app-data/code_user-123/.env_code.json" in client.containers.volumes
    assert "/srv/llm4ad/app-datacode_user-123/" not in client.containers.volumes


def test_code_server_rejects_relative_host_project_home(monkeypatch):
    client = _FakeDockerClient()
    monkeypatch.setattr(code_server_service, "get_docker_client", lambda: client)
    monkeypatch.setattr(settings, "HOST_PROJECT_HOME", "./app-data")

    with pytest.raises(HTTPException) as exc_info:
        code_server_service.get_or_start_container("code_user-123")

    assert exc_info.value.status_code == 500
    assert "HOST_PROJECT_HOME" in exc_info.value.detail
    assert "绝对路径" in exc_info.value.detail
