"""Verify ZIP import persistence, portable config and failure cleanup."""

import hashlib
import io
import json
import stat
import uuid
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app import models
from app.api.deps import get_current_user, get_db
from app.api.llm4ad.projects import router
from app.api.llm4ad.tasks import router as tasks_router
from app.services import project_package_service as service

EXAMPLE = Path(__file__).resolve().parents[4] / "skills/llm4ad-cloud-package/assets/knapsack"


class MemoryStorage:
    """Record file bytes and optionally simulate a partial upload failure."""

    def __init__(self):
        self.files = {}
        self.fail_after = None

    def upload(self, key, data):
        self.files[key] = data.read()
        if self.fail_after is not None and len(self.files) >= self.fail_after:
            raise OSError("Storage unavailable")

    def delete_many(self, keys):
        for key in keys:
            self.files.pop(key, None)


@pytest.fixture
def environment(monkeypatch):
    """Use an isolated real SQL transaction and an in-memory object store."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(
        engine, tables=[models.User.__table__, models.Project.__table__, models.Task.__table__,
                        models.UserMemoryConfig.__table__, models.ProjectMemoryConfig.__table__]
    )
    objects = MemoryStorage()
    monkeypatch.setattr(service, "storage", objects)
    with Session(engine) as session:
        user = models.User(email="cloud-package@example.com", hashed_password="unused")
        session.add(user)
        session.commit()
        session.refresh(user)
        yield session, user, objects
    engine.dispose()


@pytest.fixture
def entries():
    """Read the same portable example that is shipped in the Skill."""
    return {
        path.relative_to(EXAMPLE).as_posix(): path.read_bytes()
        for path in EXAMPLE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def archive(entries, *, report=True):
    """Build a single-root archive with matching local report hashes."""
    content = dict(entries)
    if report:
        content["validation.json"] = json.dumps(
            {
                "status": "passed",
                "files_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in content.items()},
            }
        ).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, data in content.items():
            package.writestr(f"knapsack/{name}", data)
    stream.seek(0)
    return stream


def test_import_preserves_context_and_owns_task_without_executing_code(environment, entries):
    db, user, objects = environment
    config = yaml.safe_load(entries["config.yaml"])
    config["coder"]["context_max_tokens"] = 32768
    config["planner"]["provider_model"] = "another-users-model"
    entries["config.yaml"] = yaml.safe_dump(config).encode()
    entries["knapsack_evaluator.py"] = b"raise RuntimeError('Do not execute during import')\n"
    result = service.import_project_package(db, user, archive(entries), name="Imported task", language="zh")
    project = db.get(models.Project, result.project_id)
    task = db.get(models.Task, result.task_id)
    assert project.user_id == user.id
    assert project.name == "Imported task"
    assert task.project_id == project.id
    assert task.status == models.TaskStatus.UNINITIALIZED
    assert task.ai_built is False
    assert task.input_args["coder"]["context_max_tokens"] == 32768
    assert task.input_args["coder"]["max_gen_tokens"] == config["coder"]["max_gen_tokens"]
    assert task.input_args["repo_analyzer"] == config["repo_analyzer"]
    assert task.input_args["planner"]["samplers"] == config["planner"]["samplers"]
    assert task.input_args["planner"]["provider_model"] == ""
    assert task.input_args["providers"] == []
    stored = {key.removeprefix(f"{task.input_data_path}/knapsack/"): data for key, data in objects.files.items()}
    assert result.file_count == len(entries) + 1
    for name, data in entries.items():
        assert stored[name] == data


def test_desktop_zip_metadata_does_not_change_the_task_root(environment, entries):
    """Ignore macOS/Windows metadata without ignoring actual package files."""
    db, user, objects = environment
    stream = archive(entries)
    with zipfile.ZipFile(stream, "a") as package:
        package.writestr("__MACOSX/._knapsack", b"resource fork")
        package.writestr("knapsack/.DS_Store", b"finder")
        package.writestr("knapsack/Thumbs.db", b"explorer")
    result = service.import_project_package(db, user, stream)
    assert result.file_count == len(entries) + 1
    assert not any("__MACOSX" in key or ".DS_Store" in key or "Thumbs.db" in key for key in objects.files)


@pytest.mark.parametrize("base,pattern", [("data/sample", "*.json"), (".", "data/sample/*.json")])
def test_import_resolves_glob_against_its_configured_base(environment, entries, base, pattern):
    """Use the runtime's glob.path semantics when checking an uploaded dataset."""
    db, user, _ = environment
    config = yaml.safe_load(entries["config.yaml"])
    config["evaluator"]["dataset"] = {"mode": "glob", "path": base, "pattern": pattern}
    entries["config.yaml"] = yaml.safe_dump(config).encode()
    result = service.import_project_package(db, user, archive(entries))
    task = db.get(models.Task, result.task_id)
    assert task.input_args["evaluator"]["dataset"] == config["evaluator"]["dataset"]


def test_import_rejects_a_glob_that_selects_directories(environment, entries):
    """An importer must not hide a directory entry that the dispatcher will receive."""
    db, user, _ = environment
    config = yaml.safe_load(entries["config.yaml"])
    config["evaluator"]["dataset"] = {"mode": "glob", "pattern": "data/*"}
    entries["config.yaml"] = yaml.safe_dump(config).encode()
    with pytest.raises(HTTPException, match="files, not directories"):
        service.import_project_package(db, user, archive(entries))


@pytest.mark.parametrize("path", ["../escape.py", "/tmp/escape.py", "C:/escape.py", "folder\\escape.py"])
def test_rejects_archive_path_escape(environment, entries, path):
    db, user, objects = environment
    stream = archive(entries)
    with zipfile.ZipFile(stream, "a") as package:
        package.writestr(path, b"bad")
    with pytest.raises(HTTPException, match="package-relative"):
        service.import_project_package(db, user, stream)
    assert not objects.files
    assert not db.exec(select(models.Project)).all()


def test_rejects_links_and_duplicate_paths(environment, entries):
    db, user, _ = environment
    stream = archive(entries)
    info = zipfile.ZipInfo("knapsack/link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(stream, "a") as package:
        package.writestr(info, "../../outside")
    with pytest.raises(HTTPException, match="links"):
        service.import_project_package(db, user, stream)
    stream = archive(entries)
    with zipfile.ZipFile(stream, "a") as package, pytest.warns(UserWarning, match="Duplicate"):
        package.writestr("knapsack/config.yaml", entries["config.yaml"])
    with pytest.raises(HTTPException, match="Duplicate archive path"):
        service.import_project_package(db, user, stream)


def test_rejects_expanded_size_before_upload(environment, entries, monkeypatch):
    db, user, objects = environment
    monkeypatch.setattr(service.settings, "TASK_MAX_STORAGE_MB", 1)
    entries["large.txt"] = b"x" * (1024 * 1024)
    with pytest.raises(HTTPException, match="Expanded archive"):
        service.import_project_package(db, user, archive(entries))
    assert not objects.files


def test_rejects_changed_snapshot(environment, entries):
    db, user, objects = environment
    entries["validation.json"] = json.dumps({"files_sha256": {"algorithm/solver.py": "incorrect"}}).encode()
    with pytest.raises(HTTPException, match="differ from validation.json"):
        service.import_project_package(db, user, archive(entries, report=False))
    assert not objects.files


@pytest.mark.parametrize("change", ["missing-data", "absolute-source", "credentials", "unknown-method", "missing-name"])
def test_rejects_invalid_config_before_creating_project(environment, entries, change):
    db, user, objects = environment
    config = yaml.safe_load(entries["config.yaml"])
    if change == "missing-data":
        config["evaluator"]["dataset"] = {"mode": "files", "files": ["missing.json"]}
    elif change == "absolute-source":
        config["version_control"]["local_path"] = "/tmp/algorithm"
    elif change == "credentials":
        config["providers"] = [{"name": "default", "api_key": "not-portable"}]
    elif change == "missing-name":
        del config["project_name"]
    else:
        config["evolution"]["type"] = "not-an-algorithm"
    entries["config.yaml"] = yaml.safe_dump(config).encode()
    with pytest.raises(HTTPException) as error:
        service.import_project_package(db, user, archive(entries))
    assert error.value.status_code == 400
    assert not objects.files
    assert not db.exec(select(models.Project)).all()


def test_storage_failure_rolls_back_project_task_and_partial_upload(environment, entries):
    db, user, objects = environment
    objects.fail_after = 2
    with pytest.raises(OSError, match="Storage unavailable"):
        service.import_project_package(db, user, archive(entries))
    assert not objects.files
    assert not db.exec(select(models.Project)).all()
    assert not db.exec(select(models.Task)).all()


def test_database_failure_cleans_uploaded_files(environment, entries, monkeypatch):
    db, user, objects = environment

    def fail_commit():
        raise RuntimeError("Database unavailable")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="Database unavailable"):
        service.import_project_package(db, user, archive(entries))
    assert not objects.files
    assert not db.exec(select(models.Project)).all()


def test_multipart_route_creates_project_and_requires_login(environment, entries):
    db, user, _ = environment
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    assert (
        client.post("/api/v1/projects/import-package", files={"file": ("task.zip", archive(entries))}).status_code
        == 401
    )
    app.dependency_overrides[get_current_user] = lambda: user
    response = client.post(
        "/api/v1/projects/import-package",
        files={"file": ("task.zip", archive(entries), "application/zip")},
        data={"name": "Imported via web", "language": "en"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["task_name"] == "knapsack-cloud"
    assert response.json()["evolution_type"] == "island_ga"


def test_task_import_uses_existing_project_and_returns_the_configured_task(environment, entries):
    """Import into the selected project and preserve its identity and metadata."""
    db, user, objects = environment
    project = models.Project(name="Existing project", description="Keep this description", user_id=user.id)
    db.add(project)
    db.commit()
    project_id = project.id
    task = service.import_task_package(db, user, project_id, archive(entries), name="Imported scheduling task")
    assert task.project_id == project_id
    assert task.name == task.input_args["project_name"] == "Imported scheduling task"
    assert task.input_args["providers"] == []
    assert task.input_args["coder"]["context_max_tokens"] == 16384
    assert task.status == models.TaskStatus.UNINITIALIZED
    assert task.input_args["memory"]["enabled"] is True
    assert task.input_args["memory"]["type"] == "local_yaml"
    assert db.get(models.Project, project_id).name == "Existing project"
    assert db.get(models.Project, project_id).description == "Keep this description"
    assert len(db.exec(select(models.Project)).all()) == 1
    assert len(db.exec(select(models.Task)).all()) == 1
    assert len(objects.files) == len(entries) + 1


def test_failed_task_import_keeps_existing_project_and_tasks(environment, entries):
    """A failed upload removes only the new task and its partial object uploads."""
    db, user, objects = environment
    project = models.Project(name="Keep project", user_id=user.id)
    db.add(project)
    db.flush()
    existing = models.Task(name="Keep task", project_id=project.id)
    db.add(existing)
    db.commit()
    project_id, existing_id = project.id, existing.id
    objects.fail_after = 2
    with pytest.raises(OSError, match="Storage unavailable"):
        service.import_task_package(db, user, project_id, archive(entries))
    assert [p.id for p in db.exec(select(models.Project)).all()] == [project_id]
    assert [t.id for t in db.exec(select(models.Task)).all()] == [existing_id]
    assert not objects.files


@pytest.mark.parametrize("missing", [False, True])
def test_task_import_checks_target_project_before_reading_archive(environment, missing):
    """Reject nonexistent and inaccessible projects without parsing or uploading."""
    db, user, objects = environment
    other = models.User(email="another-owner@example.com", hashed_password="unused")
    db.add(other)
    db.flush()
    project = models.Project(name="Private project", user_id=other.id)
    db.add(project)
    db.commit()
    target = uuid.uuid4() if missing else project.id
    with pytest.raises(HTTPException) as error:
        service.import_task_package(db, user, target, io.BytesIO(b"not a ZIP"))
    assert error.value.status_code == (404 if missing else 403)
    assert not objects.files
    assert not db.exec(select(models.Task)).all()


@pytest.mark.parametrize("enable_memory", [None, "false"])
def test_task_multipart_route_imports_under_selected_project(environment, entries, enable_memory):
    """The task dialog can select and display the created task from one response."""
    db, user, _ = environment
    project = models.Project(name="Target project", user_id=user.id)
    db.add(project)
    db.commit()
    app = FastAPI()
    app.include_router(tasks_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    url = "/api/v1/tasks/import-package"
    assert client.post(url, files={"file": ("task.zip", archive(entries))}).status_code == 401
    app.dependency_overrides[get_current_user] = lambda: user
    assert client.post(url, files={"file": ("task.zip", archive(entries))}).status_code == 422
    form = {"project_id": str(project.id), "name": "ZIP task", "language": "zh"}
    if enable_memory is not None:
        form["enable_memory"] = enable_memory
    response = client.post(url, files={"file": ("task.zip", archive(entries))}, data=form)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["project_id"] == str(project.id)
    assert data["name"] == data["input_args"]["project_name"] == "ZIP task"
    assert data["input_data_path"]
    assert data["input_args"]["memory"]["enabled"] is (enable_memory is None)
    assert len(db.exec(select(models.Project)).all()) == 1


@pytest.mark.parametrize("bound,valid,available,expected", [
    (False, True, True, "local_yaml"),
    (True, True, True, "mindmemos_cloud"),
    (True, False, True, "local_yaml"),
    (True, True, False, "local_yaml"),
])
def test_task_import_enables_memory_from_platform_settings(environment, entries, monkeypatch, bound, valid, available, expected):
    """Old offline packages should inherit valid bindings without importing another identity."""
    from app.services import memory_service

    db, user, _ = environment
    project = models.Project(name="Memory project", user_id=user.id)
    db.add(project)
    db.add(models.UserMemoryConfig(user_id=user.id, mindmemos_binding_id="binding" if bound else None))
    db.flush()
    db.add(models.ProjectMemoryConfig(project_id=project.id, include_project_memory=True, project_memory_limit=7))
    db.commit()
    monkeypatch.setattr(service.settings, "LLM4AD_MINDMEMOS_ENABLED", available)
    monkeypatch.setattr(service.settings, "LLM4AD_MINDMEMOS_BASE_URL", "http://memory.test")
    monkeypatch.setattr(service.settings, "LLM4AD_MINDMEMOS_JWT_SECRET", "test-secret")
    monkeypatch.setattr(memory_service, "get_mindmemos_provider_binding_error", lambda *_: None if valid else "invalid binding")
    config = yaml.safe_load(entries["config.yaml"])
    config["memory"] = {"enabled": False, "type": "mindmemos_cloud", "mindmemos_api_key": "old-key",
                        "mindmemos_user_id": "another-user",
                        "auto_extraction": {"enabled": True, "max_cards_per_generation": 2}}
    entries["config.yaml"] = yaml.safe_dump(config).encode()
    task = service.import_task_package(db, user, project.id, archive(entries))
    memory = task.input_args["memory"]
    assert memory["enabled"] is True and memory["type"] == expected
    assert memory["auto_extraction"]["enabled"] is True
    assert memory["auto_extraction"]["max_cards_per_generation"] == 2
    assert memory["include_project_memory"] is True and memory["project_memory_limit"] == 7
    assert "mindmemos_api_key" not in memory and "mindmemos_user_id" not in memory


def test_task_import_can_explicitly_disable_memory(environment, entries):
    """An explicit unchecked import option takes precedence over package defaults."""
    db, user, _ = environment
    project = models.Project(name="Offline import", user_id=user.id)
    db.add(project)
    db.commit()
    task = service.import_task_package(db, user, project.id, archive(entries), enable_memory=False)
    assert task.input_args["memory"]["enabled"] is False
    assert task.input_args["memory"]["type"] == "local_yaml"
