import uuid
import zipfile
import re
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import models
from app.core.config import settings
from app.core.db import engine
from app.core.security import create_access_token
from app.services.task_service.workspace_download import build_workspace_archive
from tests.utils.user import create_random_user


@pytest.fixture(scope="module")
def db():
    with Session(engine) as session:
        yield session


def _zip_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def _download_filename(response) -> str:
    disposition = response.headers["content-disposition"]
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition)
    if match:
        return unquote(match.group(1))
    match = re.search(r'filename="([^"]+)"', disposition)
    assert match is not None
    return match.group(1)


def test_build_workspace_archive_keeps_logs_and_excludes_dependencies_and_temp_files(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "main.py").write_text("print('hello')\n")
    (workspace / "run.log").write_text("important diagnostic log\n")
    (workspace / ".git" / "refs" / "heads").mkdir(parents=True)
    (workspace / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (workspace / ".git" / "refs" / "heads" / "main").write_text("abc123\n")
    (workspace / "node_modules" / "pkg").mkdir(parents=True)
    (workspace / "node_modules" / "pkg" / "index.js").write_text("dependency\n")
    (workspace / ".venv" / "lib").mkdir(parents=True)
    (workspace / ".venv" / "lib" / "site.py").write_text("dependency\n")
    (workspace / "__pycache__").mkdir()
    (workspace / "__pycache__" / "main.pyc").write_bytes(b"compiled")
    (workspace / "scratch.tmp").write_text("temp\n")

    archive_path, _ = build_workspace_archive(workspace, "task-1")

    try:
        names = _zip_names(archive_path)
    finally:
        archive_path.unlink(missing_ok=True)

    assert "src/main.py" in names
    assert "run.log" in names
    assert ".git/HEAD" in names
    assert ".git/refs/heads/main" in names
    assert "node_modules/pkg/index.js" not in names
    assert ".venv/lib/site.py" not in names
    assert "__pycache__/main.pyc" not in names
    assert "scratch.tmp" not in names


def test_build_workspace_archive_skips_symlinks_that_escape_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret\n")
    (workspace / "safe.txt").write_text("safe\n")

    try:
        (workspace / "escape.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not supported in this environment")

    archive_path, _ = build_workspace_archive(workspace, "task-1")

    try:
        names = _zip_names(archive_path)
    finally:
        archive_path.unlink(missing_ok=True)

    assert "safe.txt" in names
    assert "escape.txt" not in names


def test_build_workspace_archive_rejects_oversized_workspaces(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("12345")

    with pytest.raises(HTTPException) as exc:
        build_workspace_archive(workspace, "task-1", max_uncompressed_bytes=4)

    assert exc.value.status_code == 413


def test_build_workspace_archive_rejects_missing_workspace(tmp_path: Path):
    with pytest.raises(HTTPException) as exc:
        build_workspace_archive(tmp_path / "missing", "task-1")

    assert exc.value.status_code == 404


def _create_task_for_user(
    db: Session,
    user_id,
    *,
    project_name: str = "Workspace Download",
    task_name: str = "Downloadable Task",
) -> models.Task:
    project = models.Project(name=project_name, description="", user_id=user_id)
    db.add(project)
    db.commit()
    db.refresh(project)

    task = models.Task(name=task_name, project_id=project.id)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _workspace_path(root: Path, user_id: uuid.UUID, task_id: uuid.UUID) -> Path:
    return root / f"code_user-{user_id}" / str(task_id)


def _auth_headers(user: models.User) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(user.id, expires_delta=timedelta(minutes=5))}"
    }


def test_workspace_download_endpoint_requires_authentication(
    client: TestClient,
):
    response = client.get(f"{settings.API_V1_STR}/llm4ad/tasks/{uuid.uuid4()}/workspace/download")

    assert response.status_code == 401


def test_workspace_download_endpoint_returns_authorized_workspace_zip(
    client: TestClient,
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)

    workspace = _workspace_path(tmp_path, user.id, task.id)
    workspace.mkdir(parents=True)
    (workspace / "main.py").write_text("print('ok')\n")
    (workspace / "run.log").write_text("keep this\n")
    monkeypatch.setattr(settings, "DOCKER_PROJECT_HOME", str(tmp_path))

    response = client.get(
        f"{settings.API_V1_STR}/llm4ad/tasks/{task.id}/workspace/download",
        headers=_auth_headers(user),
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    filename = _download_filename(response)
    assert str(task.id) not in filename
    assert re.fullmatch(
        r"LLM4AD-Workspace-Download-Downloadable-Task-v0-\d{8}-\d{6}-workspace\.zip",
        filename,
    )
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"main.py", "run.log"}


def test_workspace_download_endpoint_names_child_version_without_task_id(
    client: TestClient,
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    root = _create_task_for_user(
        db,
        user.id,
        project_name="Project / With Unsafe Characters",
        task_name="Root Task",
    )
    root.created_time = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    db.add(root)
    db.commit()
    db.refresh(root)
    child = models.Task(
        name='Child: "Better" Version',
        project_id=root.project_id,
        group_id=root.id,
        parent_id=root.id,
        created_time=datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
    )
    db.add(child)
    db.commit()
    db.refresh(child)

    workspace = _workspace_path(tmp_path, user.id, child.id)
    workspace.mkdir(parents=True)
    (workspace / "main.py").write_text("print('child')\n")
    monkeypatch.setattr(settings, "DOCKER_PROJECT_HOME", str(tmp_path))

    response = client.get(
        f"{settings.API_V1_STR}/llm4ad/tasks/{child.id}/workspace/download",
        headers=_auth_headers(user),
    )

    assert response.status_code == 200
    filename = _download_filename(response)
    assert str(child.id) not in filename
    assert "/" not in filename
    assert ":" not in filename
    assert '"' not in filename
    assert re.fullmatch(
        r"LLM4AD-Project-With-Unsafe-Characters-Child-Better-Version-v1-\d{8}-\d{6}-workspace\.zip",
        filename,
    )


def test_workspace_download_endpoint_does_not_use_legacy_task_root(
    client: TestClient,
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    user = create_random_user(db)
    task = _create_task_for_user(db, user.id)

    legacy_workspace = tmp_path / str(task.id)
    legacy_workspace.mkdir()
    (legacy_workspace / "wrong.txt").write_text("must not be downloaded\n")
    monkeypatch.setattr(settings, "DOCKER_PROJECT_HOME", str(tmp_path))

    response = client.get(
        f"{settings.API_V1_STR}/llm4ad/tasks/{task.id}/workspace/download",
        headers=_auth_headers(user),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "任务工作区不存在，请先运行任务"


def test_workspace_download_endpoint_superuser_downloads_task_owner_workspace(
    client: TestClient,
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    owner = create_random_user(db)
    task = _create_task_for_user(db, owner.id)
    admin = create_random_user(db)
    admin.is_superuser = True
    db.add(admin)
    db.commit()
    db.refresh(admin)

    owner_workspace = _workspace_path(tmp_path, owner.id, task.id)
    owner_workspace.mkdir(parents=True)
    (owner_workspace / "owner.txt").write_text("owner workspace\n")

    admin_workspace = _workspace_path(tmp_path, admin.id, task.id)
    admin_workspace.mkdir(parents=True)
    (admin_workspace / "admin.txt").write_text("admin workspace\n")
    monkeypatch.setattr(settings, "DOCKER_PROJECT_HOME", str(tmp_path))

    response = client.get(
        f"{settings.API_V1_STR}/llm4ad/tasks/{task.id}/workspace/download",
        headers=_auth_headers(admin),
    )

    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"owner.txt"}


def test_workspace_download_endpoint_rejects_other_users(
    client: TestClient,
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    owner = create_random_user(db)
    task = _create_task_for_user(db, owner.id)

    workspace = _workspace_path(tmp_path, owner.id, task.id)
    workspace.mkdir(parents=True)
    (workspace / "main.py").write_text("print('ok')\n")
    monkeypatch.setattr(settings, "DOCKER_PROJECT_HOME", str(tmp_path))

    other_user = create_random_user(db)

    response = client.get(
        f"{settings.API_V1_STR}/llm4ad/tasks/{task.id}/workspace/download",
        headers=_auth_headers(other_user),
    )

    assert response.status_code == 403
