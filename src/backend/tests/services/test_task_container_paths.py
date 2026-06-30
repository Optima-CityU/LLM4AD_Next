import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services.container_service import validate_task_container_host_path


def test_task_container_host_path_rejects_relative_path(monkeypatch):
    monkeypatch.setattr(settings, "HOST_PROJECT_HOME", "./app-data")

    with pytest.raises(HTTPException) as exc_info:
        validate_task_container_host_path()

    assert exc_info.value.status_code == 500
    assert "HOST_PROJECT_HOME" in exc_info.value.detail
    assert "绝对路径" in exc_info.value.detail


@pytest.mark.parametrize("host_project_home", ["/srv/llm4ad/app-data", r"D:\data\project_home"])
def test_task_container_host_path_accepts_absolute_paths(monkeypatch, host_project_home):
    monkeypatch.setattr(settings, "HOST_PROJECT_HOME", host_project_home)

    validate_task_container_host_path()
