"""Regress standalone design, owned runtime files, and editable task versions."""

import copy
import hashlib
import io
import json
import os
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock

import pytest
import yaml
from fastapi import HTTPException
from llm4ad.config import AppConfig
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app import models
from app.core import storage as storage_module
from app.core.config import settings
from app.schemas.autodesign import (
    AutoDesignFileUpdate,
    AutoDesignOperationRequest,
    AutoDesignPublishRequest,
    AutoDesignSessionCreate,
)
from app.schemas.task import TaskRunResponse, TaskUpdate
from app.services import autodesign_service as service
from app.services import paper_service, project_package_service, task_service
from app.services.task_service import _helpers as task_helpers
from app.services.task_service import crud as task_crud
from app.services.task_service import files as task_files

EXAMPLE = Path(__file__).resolve().parents[4] / "skills/llm4ad-cloud-package/assets/knapsack"


class MemoryStorage:
    """Keep canonical input bytes separate from temporary runtime files."""

    def __init__(self):
        """Create an empty object store."""
        self.files = {}

    def upload(self, key, data, **_kwargs):
        """Store bytes from the same inputs accepted by the real storage API."""
        self.files[key] = data if isinstance(data, bytes) else data.read()

    def download(self, key, **_kwargs):
        """Return the original bytes for one object."""
        return self.files[key]

    def list_objects(self, prefix, **_kwargs):
        """List keys beneath the requested storage prefix."""
        return sorted(key for key in self.files if key.startswith(prefix))

    def delete(self, key, **_kwargs):
        """Remove one object if it exists."""
        self.files.pop(key, None)

    def delete_many(self, keys, **_kwargs):
        """Remove only the specified objects."""
        for key in keys:
            self.delete(key)

    def copy_objects(self, src_prefix, dst_prefix, **_kwargs):
        """Copy canonical input without sharing its writable namespace."""
        copied = []
        for key in self.list_objects(src_prefix.rstrip("/") + "/"):
            target = dst_prefix.rstrip("/") + key[len(src_prefix.rstrip("/")) :]
            self.files[target] = self.files[key]
            copied.append(target)
        return copied

    def download_files_local(self, key, local_path, *, strip_first_level=False, dir_prefix="", **_kwargs):
        """Materialize stored input for the production service's filesystem reader."""
        prefix = key.rstrip("/") + "/"
        keys = self.list_objects(prefix)
        for name in keys:
            relative = name[len(prefix) :]
            if strip_first_level:
                relative = relative.split("/", 1)[1]
            if dir_prefix:
                relative = dir_prefix + relative
            target = Path(local_path) / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.files[name])
        return bool(keys)

    def get_prefix_total_size(self, prefix, **_kwargs):
        """Measure the bytes in one canonical task input prefix."""
        return sum(len(self.files[key]) for key in self.list_objects(prefix))

    def ensure_bucket(self, **_kwargs):
        """Match the storage API without making an external request."""


@pytest.fixture
def environment(monkeypatch, tmp_path):
    """Use only an isolated SQLite database, memory objects, and temporary paths."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(
        engine,
        tables=[
            model.__table__
            for model in (
                models.User,
                models.LLMProvider,
                models.EmbeddingProvider,
                models.UserDefaultModel,
                models.Project,
                models.Task,
                models.TaskLog,
                models.UserMemoryConfig,
                models.ProjectMemoryConfig,
                models.PaperWorkspace,
                models.PaperSourceVersion,
                models.AutoDesignSession,
            )
        ],
    )
    objects = MemoryStorage()
    for module in (storage_module, service, project_package_service, task_crud, task_helpers, task_files):
        if hasattr(module, "storage"):
            monkeypatch.setattr(module, "storage", objects)
    monkeypatch.setattr(paper_service, "_storage", lambda: objects)
    monkeypatch.setattr(settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    with Session(engine) as db:
        user = models.User(email="autodesign-owner@example.com", hashed_password="unused")
        other = models.User(email="autodesign-other@example.com", hashed_password="unused")
        db.add_all([user, other])
        db.commit()
        db.refresh(user)
        db.refresh(other)
        yield SimpleNamespace(db=db, user=user, other=other, storage=objects, home=tmp_path)
    engine.dispose()


@pytest.fixture
def entries():
    """Read the actual portable Cloud knapsack evaluator, candidate, and dataset."""
    return {
        path.relative_to(EXAMPLE).as_posix(): path.read_bytes()
        for path in EXAMPLE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


@pytest.fixture
def config(entries):
    """Validate the shipped fixture with the real AppConfig schema."""
    result = yaml.safe_load(entries["config.yaml"])
    parsed = AppConfig.from_dict(copy.deepcopy(result))
    assert parsed.evaluator.module == "knapsack_evaluator.py:KnapsackEvaluator"
    return result


@pytest.fixture
def make_task(environment, entries, config):
    """Persist owned tasks and canonical input without submitting any execution."""

    def create(*, owner=None, project=None, parent=None, name="Knapsack baseline"):
        user = owner if owner is not None else environment.user
        db = environment.db
        if project is None:
            project = models.Project(name="AutoDesign regression", user_id=user.id)
            db.add(project)
            db.flush()
        task = models.Task(
            project_id=project.id,
            name=name,
            input_args=copy.deepcopy(config),
            status=models.TaskStatus.COMPLETED,
            parent_id=parent.id if parent is not None else None,
            group_id=(parent.group_id or parent.id) if parent is not None else None,
            reports={"analysis": {"summary": "Parent report"}},
            result_render={"summary": "Parent visualization"},
            celery_task_id="previous-run",
        )
        task.input_data_path = f"tasks/{task.id}/canonical"
        db.add(task)
        db.flush()
        db.add(
            models.TaskLog(
                task_id=task.id, type="generated", message="Parent candidate", data={"data": {"evaluation": {"score": 42.0}}}
            )
        )
        db.commit()
        db.refresh(task)
        for path, content in entries.items():
            environment.storage.upload(f"{task.input_data_path}/knapsack/{path}", content)
        return task

    return create


def runtime_root(environment, task):
    """Locate the actual task-runner directory under the project's owner."""
    project = environment.db.get(models.Project, task.project_id)
    return environment.home / f"code_user-{project.user_id}" / str(task.id)


def write_file(root, relative, content):
    """Write a runtime or draft artifact beneath a temporary root."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def child_from_result(environment, result):
    """Resolve the returned child through SQL rather than mocked task records."""
    child = environment.db.get(models.Task, uuid.UUID(str(result["task_id"])))
    assert child is not None
    assert result["draft_path"]
    return child


def test_standalone_session_needs_no_paper_and_creates_no_execution_task(environment):
    """Persist a native conversation before any source or execution task exists."""
    db, user = environment.db, environment.user
    response = service.create_session(db, user, AutoDesignSessionCreate(title="Design a knapsack solver"))
    workspace = db.get(models.PaperWorkspace, response.id)
    session = db.get(models.AutoDesignSession, response.id)

    assert workspace.user_id == user.id
    assert workspace.active_source_version_id is None
    assert session is not None
    assert session.task_id is None
    assert response.task_id is None
    assert response.project_ready is False
    assert not db.exec(select(models.Task)).all()
    assert not db.exec(select(models.PaperSourceVersion)).all()
    assert service.get_session(db, user, response.id).id == response.id
    context = service.get_context(db, user, response.id)
    assert context.task is None
    assert context.family == []


def test_resolve_session_is_stable_per_task_version(environment, make_task):
    """Reuse each task's conversation while giving child versions their own identity."""
    db, user = environment.db, environment.user
    root = make_task()
    child = make_task(project=db.get(models.Project, root.project_id), parent=root)
    first = service.resolve_task_session(db, user, root.id)
    child_session = service.resolve_task_session(db, user, child.id)

    assert service.resolve_task_session(db, user, root.id).id == first.id
    assert service.resolve_task_session(db, user, child.id).id == child_session.id
    assert first.id != child_session.id
    assert first.task_id == root.id
    assert child_session.task_id == child.id
    assert first.root_task_id == child_session.root_task_id == root.id
    assert len(db.exec(select(models.AutoDesignSession)).all()) == 2
    assert len(db.exec(select(models.Task)).all()) == 2
    assert not db.exec(select(models.PaperSourceVersion)).all()


def test_task_context_reads_requested_version_and_real_persisted_stats(environment, make_task):
    """Include family versions and actual scores without following the active child implicitly."""
    db, user = environment.db, environment.user
    root = make_task()
    child = make_task(project=db.get(models.Project, root.project_id), parent=root)
    root.active_child_id = child.id
    db.add(root)
    db.commit()
    session = service.resolve_task_session(db, user, root.id)

    context = service.get_context(db, user, session.id)
    selected = service.get_context(db, user, session.id, task_id=child.id)

    assert context.task.id == root.id
    assert selected.task.id == child.id
    assert {task.id for task in context.family} == {root.id, child.id}
    assert context.root_task_id == selected.root_task_id == root.id
    assert context.stats["solution_count"] == 1
    assert context.stats["max_score"] == 42.0
    assert context.config["evaluator"]["module"] == "knapsack_evaluator.py:KnapsackEvaluator"


@pytest.mark.parametrize("action", ["resolve", "session", "context", "list", "read", "child", "patch", "start", "stop"])
def test_another_users_task_and_session_are_rejected(environment, make_task, monkeypatch, action):
    """Verify ownership through SQL before every read or mutation entry point."""
    db, owner, other = environment.db, environment.user, environment.other
    task = make_task()
    session = service.resolve_task_session(db, owner, task.id)
    start, stop = Mock(), Mock()
    monkeypatch.setattr(task_service, "run_task", start)
    monkeypatch.setattr(task_service, "stop_task", stop)

    with pytest.raises(HTTPException) as error:
        if action == "resolve":
            service.resolve_task_session(db, other, task.id)
        elif action == "session":
            service.get_session(db, other, session.id)
        elif action == "context":
            service.get_context(db, other, session.id)
        elif action == "list":
            service.list_files(db, other, session.id)
        elif action == "read":
            service.read_file(db, other, session.id, "run", "result.txt")
        elif action == "child":
            service.create_child(db, other, session.id, parent_id=task.id)
        elif action == "patch":
            service.patch_child(db, other, session.id, task.id, {"random_seed": 7})
        else:
            service.operate(db, other, session.id, AutoDesignOperationRequest(operation=action, task_id=task.id))

    assert error.value.status_code in (403, 404)
    assert len(db.exec(select(models.Task)).all()) == 1
    start.assert_not_called()
    stop.assert_not_called()


def test_create_session_rejects_another_users_project(environment):
    """Reject a foreign project before persisting an associated conversation."""
    db = environment.db
    project = models.Project(name="Private project", user_id=environment.other.id)
    db.add(project)
    db.commit()
    with pytest.raises(HTTPException) as error:
        service.create_session(db, environment.user, AutoDesignSessionCreate(project_id=project.id))
    assert error.value.status_code in (403, 404)
    assert not db.exec(select(models.AutoDesignSession)).all()


def test_read_and_list_use_actual_runtime_files_instead_of_canonical_input(environment, make_task):
    """Analyze an evolved source file from the real task-runner mount path."""
    task = make_task()
    session = service.resolve_task_session(environment.db, environment.user, task.id)
    relative = "llm4ad/run/best/code/solver.py"
    content = "# Actual evolved candidate\ndef solve(instance):\n    return {'selected': [1]}\n"
    write_file(runtime_root(environment, task), relative, content)

    listing = service.list_files(environment.db, environment.user, session.id, area="run")
    result = service.read_file(environment.db, environment.user, session.id, "run", relative)

    assert listing.task_id == task.id
    assert any(file.path == relative and file.size == len(content.encode()) for file in listing.files)
    assert result.content == content
    assert result.path == relative
    assert result.total_lines == 3


@pytest.mark.parametrize("area", ["run", "analysis"])
def test_default_file_read_preserves_the_entire_large_text(environment, make_task, area):
    """Never silently replace an explicit full-file analysis with a preview."""
    task = make_task()
    session = service.resolve_task_session(environment.db, environment.user, task.id)
    content = "".join(f"{line:05d}: " + "candidate evidence " * 8 + "\n" for line in range(1, 2501))
    root = runtime_root(environment, task) if area == "run" else paper_service.paper_workspace_root(environment.user.id, session.id) / "source/analysis"
    write_file(root, "logs/full.txt", content)

    result = service.read_file(environment.db, environment.user, session.id, area, "logs/full.txt")

    assert result.content == content
    assert result.total_lines == 2500
    assert (result.start_line, result.end_line) == (1, 2500)


@pytest.mark.parametrize(
    "start,end,expected",
    [(2, 3, ["two", "three"]), (3, None, ["three", "four"]), (None, 2, ["one", "two"]), (4, 4, ["four"])],
)
@pytest.mark.parametrize("area", ["run", "analysis"])
def test_file_line_ranges_are_explicit_one_based_and_inclusive(environment, make_task, start, end, expected, area):
    """Return requested lines alongside the original file's total line count."""
    task = make_task()
    session = service.resolve_task_session(environment.db, environment.user, task.id)
    root = runtime_root(environment, task) if area == "run" else paper_service.paper_workspace_root(environment.user.id, session.id) / "source/analysis"
    write_file(root, "logs/lines.txt", "one\ntwo\nthree\nfour\n")

    result = service.read_file(
        environment.db, environment.user, session.id, area, "logs/lines.txt", start_line=start, end_line=end
    )

    assert result.content.splitlines() == expected
    assert result.total_lines == 4
    assert result.start_line == (start if start is not None else 1)
    assert result.end_line == (end if end is not None else 4)


@pytest.mark.parametrize(
    "path", ["../outside.txt", "/tmp/outside.txt", "C:/outside.txt", "nested/../../outside.txt", r"..\outside.txt"]
)
@pytest.mark.parametrize("operation", ["read", "list"])
@pytest.mark.parametrize("area", ["run", "analysis"])
def test_runtime_paths_cannot_escape_the_scoped_root(environment, make_task, path, operation, area):
    """Apply the same relative-path boundary to file reads and directory listings."""
    task = make_task()
    session = service.resolve_task_session(environment.db, environment.user, task.id)
    write_file(runtime_root(environment, task).parent, "outside.txt", "Private sibling content")

    with pytest.raises(HTTPException) as error:
        if operation == "read":
            service.read_file(environment.db, environment.user, session.id, area, path)
        else:
            service.list_files(environment.db, environment.user, session.id, area=area, directory=path)
    assert error.value.status_code in (400, 403, 404)


@pytest.mark.parametrize("directory_link", [False, True])
@pytest.mark.parametrize("area", ["run", "analysis"])
def test_runtime_symlinks_cannot_reveal_files_outside_the_run(environment, make_task, directory_link, area):
    """Resolve symlink targets before reading or enumerating scoped runtime files."""
    task = make_task()
    session = service.resolve_task_session(environment.db, environment.user, task.id)
    run = runtime_root(environment, task) if area == "run" else paper_service.paper_workspace_root(environment.user.id, session.id) / "source/analysis"
    run.mkdir(parents=True)
    outside = write_file(environment.home / "private", "secret.txt", "Must not escape the run")
    link = run / "linked"
    link.symlink_to(outside.parent if directory_link else outside, target_is_directory=directory_link)
    path = "linked/secret.txt" if directory_link else "linked"

    with pytest.raises(HTTPException) as error:
        service.read_file(environment.db, environment.user, session.id, area, path)
    assert error.value.status_code in (400, 403, 404)
    try:
        listing = service.list_files(environment.db, environment.user, session.id, area=area)
    except HTTPException as error:
        assert error.status_code in (400, 403, 404)
    else:
        assert not any(file.path == path or file.path.startswith("linked/") for file in listing.files)


def test_context_strips_provider_credentials_from_every_task_and_stats(environment, make_task):
    """Sanitize config, family members, task responses, and nested statistics."""
    db, user = environment.db, environment.user
    root = make_task()
    child = make_task(project=db.get(models.Project, root.project_id), parent=root)
    secrets = ["sk-parent-provider-secret", "child-provider-token"]
    for task, secret in zip((root, child), secrets, strict=True):
        data = copy.deepcopy(task.input_args)
        data["providers"] = [{"name": "default", "api_key": secret, "auth_token": secret}]
        task.input_args = data
        db.add(task)
    db.commit()
    before = copy.deepcopy(root.input_args)
    session = service.resolve_task_session(db, user, root.id)

    context = service.get_context(db, user, session.id)
    serialized = context.model_dump_json()

    for secret in secrets:
        assert secret not in serialized
    assert context.config["evolution"]["max_generations"] == before["evolution"]["max_generations"]
    db.refresh(root)
    assert root.input_args == before


@pytest.mark.parametrize("area", ["run", "input", "draft", "analysis"])
def test_text_config_never_exposes_provider_secrets(environment, make_task, config, area):
    """Reject or sanitize credentials even when config.yaml is read as text."""
    task = make_task()
    session = service.resolve_task_session(environment.db, environment.user, task.id)
    secret = "sk-file-provider-secret-do-not-expose"
    config["providers"] = [{"name": "default", "api_key": secret, "auth_token": secret}]
    text = yaml.safe_dump(config)
    if area == "input":
        path = "config.yaml"
        environment.storage.upload(f"{task.input_data_path}/knapsack/{path}", text.encode())
    elif area == "draft":
        path = f"{task.id}/config.yaml"
        root = paper_service.paper_workspace_root(environment.user.id, session.id)
        write_file(root / "source" / "draft", path, text)
    elif area == "analysis":
        path = "config.yaml"
        root = paper_service.paper_workspace_root(environment.user.id, session.id)
        write_file(root / "source" / "analysis", path, text)
    else:
        path = "config.yaml"
        write_file(runtime_root(environment, task), path, text)

    try:
        result = service.read_file(environment.db, environment.user, session.id, area, path)
    except HTTPException as error:
        assert error.status_code in (400, 403)
        assert secret not in str(error.detail)
    else:
        assert secret not in result.content
        assert result.total_lines > 0


@pytest.mark.parametrize("action", ["context", "list", "read", "analysis_list", "analysis_read", "child", "patch", "start", "stop"])
def test_an_owned_but_unrelated_task_family_is_rejected(environment, make_task, monkeypatch, action):
    """Owning a project does not authorize injecting a different family into this session."""
    db, user = environment.db, environment.user
    task = make_task()
    unrelated = make_task(project=db.get(models.Project, task.project_id))
    session = service.resolve_task_session(db, user, task.id)
    start, stop = Mock(), Mock()
    monkeypatch.setattr(task_service, "run_task", start)
    monkeypatch.setattr(task_service, "stop_task", stop)
    before = copy.deepcopy(unrelated.input_args)

    with pytest.raises(HTTPException) as error:
        if action == "context":
            service.get_context(db, user, session.id, task_id=unrelated.id)
        elif action in {"list", "analysis_list"}:
            service.list_files(db, user, session.id, area="analysis" if action == "analysis_list" else "run", task_id=unrelated.id)
        elif action in {"read", "analysis_read"}:
            service.read_file(db, user, session.id, "analysis" if action == "analysis_read" else "run", "config.yaml", task_id=unrelated.id)
        elif action == "child":
            service.create_child(db, user, session.id, parent_id=unrelated.id)
        elif action == "patch":
            service.patch_child(db, user, session.id, unrelated.id, {"random_seed": 7})
        else:
            service.operate(db, user, session.id, AutoDesignOperationRequest(operation=action, task_id=unrelated.id))
    assert error.value.status_code in (400, 403, 404)
    assert len(db.exec(select(models.Task)).all()) == 2
    db.refresh(unrelated)
    assert unrelated.input_args == before
    start.assert_not_called()
    stop.assert_not_called()


def test_create_child_copies_canonical_input_and_resets_execution_outputs(environment, make_task, entries):
    """Keep evaluator/data bytes while starting a clean, independent child version."""
    db, user = environment.db, environment.user
    parent = make_task()
    session = service.resolve_task_session(db, user, parent.id)
    parent_config = copy.deepcopy(parent.input_args)
    parent_objects = dict(environment.storage.files)
    write_file(runtime_root(environment, parent), "knapsack/algorithm/solver.py", "# Stale runtime input\n")
    root = paper_service.paper_workspace_root(user.id, session.id) / "source"
    write_file(root, "analysis/design.md", "# Design records are not runnable inputs\n")

    result = service.create_child(
        db,
        user,
        session.id,
        parent_id=parent.id,
        seed="input",
        name="Tuned child",
        config_patch={"evolution": {"max_generations": 9}},
    )
    child = child_from_result(environment, result)

    assert child.parent_id == parent.id
    assert child.group_id == parent.id
    assert child.project_id == parent.project_id
    assert child.status == models.TaskStatus.UNINITIALIZED
    assert child.celery_task_id is None
    assert not child.reports
    assert not child.result_render
    assert not db.exec(select(models.TaskLog).where(models.TaskLog.task_id == child.id)).all()
    assert child.input_data_path and child.input_data_path != parent.input_data_path
    assert child.input_args["evolution"]["max_generations"] == 9
    assert child.input_args["evolution"]["num_islands"] == parent_config["evolution"]["num_islands"]
    for relative, content in entries.items():
        if relative != "config.yaml":
            assert environment.storage.download(f"{child.input_data_path}/knapsack/{relative}") == content
    db.refresh(parent)
    assert parent.input_args == parent_config
    assert parent.reports == {"analysis": {"summary": "Parent report"}}
    assert (
        db.exec(select(models.TaskLog).where(models.TaskLog.task_id == parent.id)).one().message == "Parent candidate"
    )
    assert all(environment.storage.files[key] == value for key, value in parent_objects.items())
    assert "design.md" not in service._input_entries(child)
    assert not any(name.startswith("analysis/") for name in service._input_entries(child))


def test_patch_child_changes_only_child_parameters_and_preserves_the_parent(environment, make_task):
    """Apply nested parameter edits without replacing unrelated child settings."""
    db, user = environment.db, environment.user
    parent = make_task()
    session = service.resolve_task_session(db, user, parent.id)
    child = child_from_result(environment, service.create_child(db, user, session.id, parent_id=parent.id))
    parent_before = copy.deepcopy(parent.input_args)
    child_before = copy.deepcopy(child.input_args)

    result = service.patch_child(
        db, user, session.id, child.id, {"evolution": {"max_generations": 11}, "coder": {"context_max_tokens": 32768}}
    )

    assert uuid.UUID(str(result["task_id"])) == child.id
    db.refresh(child)
    db.refresh(parent)
    assert parent.input_args == parent_before
    assert child.input_args["evolution"]["max_generations"] == 11
    assert child.input_args["evolution"]["num_islands"] == child_before["evolution"]["num_islands"]
    assert child.input_args["coder"]["context_max_tokens"] == 32768
    assert child.input_args["coder"]["max_gen_tokens"] == child_before["coder"]["max_gen_tokens"]
    assert child.input_args["evaluator"] == child_before["evaluator"]
    assert child.status == models.TaskStatus.UNINITIALIZED


def test_best_seed_uses_actual_best_code_and_preserves_evaluator_and_datasets(environment, make_task, entries):
    """Seed the child with evolved best/code files rather than the original candidate."""
    db, user = environment.db, environment.user
    parent = make_task()
    session = service.resolve_task_session(db, user, parent.id)
    best_root = "llm4ad/run/best/code"
    evolved = "# Best runtime candidate\ndef solve(instance):\n    return {'selected': [0]}\n"
    run = runtime_root(environment, parent)
    write_file(run, f"{best_root}/solver.py", evolved)
    write_file(run, f"{best_root}/helpers/ranking.py", "RANKING = 'evolved'\n")
    write_file(run, "llm4ad/run/best/metadata.json", json.dumps({"score": 99.0}))
    assert evolved.encode() != entries["algorithm/solver.py"]

    result = service.create_child(db, user, session.id, parent_id=parent.id, seed="best")
    child = child_from_result(environment, result)

    source = service.read_file(db, user, session.id, "input", "algorithm/solver.py", task_id=child.id)
    helper = service.read_file(db, user, session.id, "input", "algorithm/helpers/ranking.py", task_id=child.id)
    assert source.content == evolved
    assert helper.content == "RANKING = 'evolved'\n"
    for relative in ("knapsack_evaluator.py", "data/sample/instance_001.json", "data/sample/instance_002.json"):
        result = service.read_file(db, user, session.id, "input", relative, task_id=child.id)
        assert result.content.encode() == entries[relative]
    assert (
        environment.storage.download(f"{parent.input_data_path}/knapsack/algorithm/solver.py")
        == entries["algorithm/solver.py"]
    )


@pytest.mark.parametrize("operation,delegate", [("start", "run_task"), ("stop", "stop_task")])
def test_mutation_dispatch_delegates_to_existing_task_service(environment, make_task, monkeypatch, operation, delegate):
    """Dispatch explicit start/stop requests without calling a real runner or Celery."""
    db, user = environment.db, environment.user
    task = make_task()
    session = service.resolve_task_session(db, user, task.id)
    if operation == "start":
        task.status, task.celery_task_id = models.TaskStatus.UNINITIALIZED, None
        db.add(task)
        db.commit()
    response = TaskRunResponse(task_id=task.id, celery_task_id="mocked-submission", status=models.TaskStatus.PENDING)
    start = Mock(return_value=response)
    stop = Mock(return_value=task)
    monkeypatch.setattr(task_service, "run_task", start)
    monkeypatch.setattr(task_service, "stop_task", stop)

    result = service.operate(db, user, session.id, AutoDesignOperationRequest(operation=operation, task_id=task.id))

    selected = start if delegate == "run_task" else stop
    if operation == "start":
        selected.assert_called_once_with(db, task.id, user, ANY)
        assert isinstance(selected.call_args.args[3], str)
        assert selected.call_args.args[3]
    else:
        selected.assert_called_once_with(db, task.id, user)
    (stop if delegate == "run_task" else start).assert_not_called()
    assert isinstance(result, dict)


def test_reading_canonical_input_never_executes_the_evaluator(environment, make_task):
    """Expose real package source for analysis without importing user evaluator code."""
    task = make_task()
    session = service.resolve_task_session(environment.db, environment.user, task.id)
    relative = "knapsack_evaluator.py"
    source = "raise RuntimeError('Reading evaluator source must not execute it')\n"
    environment.storage.upload(f"{task.input_data_path}/knapsack/{relative}", source.encode())

    result = service.read_file(environment.db, environment.user, session.id, "input", relative)

    assert result.content == source


def test_imported_cloud_package_can_resolve_an_autodesign_session(environment, entries):
    """Connect AutoDesign to the existing real package import path without running it."""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        for relative, content in entries.items():
            package.writestr(f"knapsack/{relative}", content)
    archive.seek(0)
    imported = project_package_service.import_project_package(environment.db, environment.user, archive)

    session = service.resolve_task_session(environment.db, environment.user, imported.task_id)

    assert session.task_id == imported.task_id
    assert session.project_id == imported.project_id
    assert environment.db.get(models.Task, imported.task_id).status == models.TaskStatus.UNINITIALIZED


@pytest.fixture
def verified_package(monkeypatch, entries):
    """Isolate container execution while exercising verified directory handoff."""
    def validate(_workspace, project, output, _timeout):
        for name, data in entries.items():
            target = project / name
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        snapshot = {name: path.read_bytes() for name, path in service._project_files(project).items()}
        report = {
            "status": "passed", "project_name": "Verified knapsack",
            "files_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in snapshot.items()},
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report), encoding="utf-8")
        return report
    monkeypatch.setattr(service, "_run_validation", validate)


@pytest.mark.usefixtures("verified_package")
def test_agent_names_the_design_and_task_setup_uses_the_name(environment):
    """Publish the conversational project name without requiring an execution task."""
    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    result = service.operate(db, user, session.id, AutoDesignOperationRequest(operation="rename", arguments={"name": " 背包优化算法 "}))
    assert result["title"] == "背包优化算法"
    assert service.get_session(db, user, session.id).title == "背包优化算法"
    assert service.get_context(db, user, session.id).title == "背包优化算法"
    assert db.exec(select(models.Task)).all() == []
    project = paper_service.paper_workspace_root(user.id, session.id) / "source/draft/knapsack"
    project.mkdir(parents=True)
    service.validate_project(db, user, session.id, "/workspace/source/draft/knapsack")
    prepared = service.prepare_task(db, user, session.id, AutoDesignPublishRequest())
    assert prepared.task.name == "背包优化算法"
    assert db.get(models.Project, prepared.task.project_id).name == "背包优化算法"
    before = copy.deepcopy(prepared.task.input_args)
    service.update_project_name(db, user, session.id, "背包局部搜索优化")
    renamed = db.get(models.Task, prepared.task.id)
    assert renamed.name == "背包局部搜索优化"
    assert renamed.input_args == before


def test_task_list_rename_updates_its_agent_title(environment, make_task):
    """Renaming an executed task keeps its bound conversation name consistent."""
    db, user = environment.db, environment.user
    task = make_task()
    session = service.resolve_task_session(db, user, task.id)
    original_inputs = copy.deepcopy(task.input_args)
    original_version = task.version_time

    task_service.update_task(db, task.id, user, TaskUpdate(name="Renamed experiment"))

    assert service.get_session(db, user, session.id).title == "Renamed experiment"
    assert task.input_args == original_inputs
    assert task.version_time == original_version


def test_task_list_rename_preserves_other_versions_names(environment, make_task):
    """Only the conversation bound to the renamed version changes its title."""
    db, user = environment.db, environment.user
    root = make_task(name="Parent experiment")
    child = make_task(parent=root, project=db.get(models.Project, root.project_id), name="Child experiment")
    parent_session = service.resolve_task_session(db, user, root.id)
    child_session = service.resolve_task_session(db, user, child.id)

    task_service.update_task(db, child.id, user, TaskUpdate(name="Renamed child"))

    assert service.get_session(db, user, child_session.id).title == "Renamed child"
    assert service.get_session(db, user, parent_session.id).title == "Parent experiment"
    assert root.name == "Parent experiment"


@pytest.mark.parametrize("name", [None, "", "   ", "x" * 256])
def test_project_naming_checks_ownership_and_valid_text(environment, name):
    """Invalid or foreign title updates never alter the current design."""
    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    request = AutoDesignOperationRequest(operation="rename", arguments={"name": name})
    with pytest.raises(HTTPException) as invalid:
        service.operate(db, user, session.id, request)
    assert invalid.value.status_code == 422
    with pytest.raises(HTTPException) as foreign:
        service.operate(db, environment.other, session.id, request)
    assert foreign.value.status_code == 404
    assert service.get_session(db, user, session.id).title == session.title


@pytest.mark.usefixtures("verified_package")
@pytest.mark.parametrize("outcome", ["passed", "failed", "exception"])
def test_verification_uses_temporary_results_without_generating_records(environment, monkeypatch, outcome):
    """Keep actual verification state while cleaning execution files on every exit."""
    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    root = paper_service.paper_workspace_root(user.id, session.id)
    project = root / "source/draft/knapsack"
    project.mkdir(parents=True)
    validator = service._run_validation
    outputs = []

    def run(workspace, draft, output, timeout):
        outputs.append(output)
        assert output.is_relative_to(root / ".research")
        progress = [{"id": "evaluation", "status": "running"}]
        output.with_suffix(".progress.json").write_text(json.dumps({"checks": progress}))
        public = service.get_session(db, user, session.id).validation_report
        assert public["checks"] == progress
        assert "progress_path" not in public
        result = validator(workspace, draft, output, timeout)
        if outcome == "exception":
            raise RuntimeError("Real evaluator execution failed")
        if outcome == "failed":
            return {"status": "failed", "error": "Real evaluator rejected the baseline"}
        return result

    monkeypatch.setattr(service, "_run_validation", run)
    result = service.validate_project(db, user, session.id, "/workspace/source/draft/knapsack")
    assert result["project_ready"] is (outcome == "passed")
    assert result["validation_report"]["status"] == ("passed" if outcome == "passed" else "failed")
    assert outputs and all(not output.parent.exists() for output in outputs)
    assert not (root / "source/analysis").exists()
    assert not environment.storage.files
    assert "report_path" not in result["validation_report"]
    assert "progress_path" not in result["validation_report"]


@pytest.mark.usefixtures("verified_package")
def test_verified_standalone_publication_reuses_conversation_and_is_idempotent(environment):
    """Retrying preparation keeps one task and its original verified directory."""
    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    draft = paper_service.paper_workspace_root(user.id, session.id) / "source/draft/knapsack"
    draft.mkdir(parents=True)
    report = service.validate_package(db, user, session.id, "/workspace/source/draft/knapsack")
    assert report["project_ready"] is True
    assert report["validation_report"]["project_path"] == "/workspace/source/draft/knapsack"
    assert "package_path" not in report["validation_report"]
    assert "report_path" not in report["validation_report"]
    assert "progress_path" not in report["validation_report"]
    assert not (draft.parent.parent / "analysis").exists()
    assert not list((draft.parent.parent.parent / ".research").glob("validation-*"))
    assert not any(key.startswith("autodesign/") for key in environment.storage.files)
    published = service.publish_package(db, user, session.id, AutoDesignPublishRequest())
    retried = service.publish_package(db, user, session.id, AutoDesignPublishRequest())
    assert published.task.id == retried.task.id
    assert len(db.exec(select(models.Task)).all()) == 1
    assert len(db.exec(select(models.Project)).all()) == 1
    assert service.resolve_task_session(db, user, published.task.id).id == session.id
    assert published.task.input_args["memory"]["enabled"] is True
    assert published.task.status == models.TaskStatus.UNINITIALIZED
    assert published.session.project_revision == report["project_revision"]
    context = service.get_context(db, user, session.id)
    assert context.draft_path == context.selected_project_path == "/workspace/source/draft/knapsack"
    assert not (draft.parent / str(published.task.id)).exists()
    legacy_copy = draft.parent / str(published.task.id)
    legacy_copy.mkdir()
    (legacy_copy / "config.yaml").write_bytes((draft / "config.yaml").read_bytes())
    (legacy_copy / "README.md").write_text("Legacy duplicate", encoding="utf-8")
    context = service.get_context(db, user, session.id)
    assert [project.path for project in context.projects] == [context.draft_path]
    files = service.list_files(db, user, session.id, "draft").files
    assert files and all(file.path.startswith("knapsack/") for file in files)
    assert legacy_copy.is_dir()
    assert not list(environment.home.rglob("*.zip"))
    assert not any(key.endswith(".zip") for key in environment.storage.files)


@pytest.mark.usefixtures("verified_package")
def test_publish_into_child_updates_canonical_files_and_preserves_parent(environment, make_task):
    """Source changes target only the explicitly selected, unstarted child."""
    db, user = environment.db, environment.user
    parent = make_task()
    before = copy.deepcopy(parent.input_args)
    session = service.resolve_task_session(db, user, parent.id)
    child = child_from_result(environment, service.create_child(db, user, session.id))
    service.validate_package(db, user, session.id, f"/workspace/source/draft/{child.id}")
    published = service.publish_package(db, user, session.id, AutoDesignPublishRequest(task_id=child.id, enable_memory=False))
    assert published.task.id == child.id
    assert child.input_args["memory"]["enabled"] is False
    assert child.input_data_path != parent.input_data_path
    assert "knapsack_evaluator.py" in service._input_entries(child)
    db.refresh(parent)
    assert parent.input_args == before
    assert len(db.exec(select(models.Task)).all()) == 2
    with pytest.raises(HTTPException) as error:
        service.publish_package(db, user, session.id, AutoDesignPublishRequest(task_id=parent.id))
    assert error.value.status_code == 409


@pytest.mark.usefixtures("verified_package")
def test_failed_revalidation_clears_project_readiness(environment, monkeypatch):
    """A failed attempt cannot leave an earlier directory revision ready for task setup."""
    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    draft = paper_service.paper_workspace_root(user.id, session.id) / "source/draft/knapsack"
    draft.mkdir(parents=True)
    service.validate_package(db, user, session.id, "/workspace/source/draft/knapsack")
    monkeypatch.setattr(service, "_run_validation", lambda *_args: {"status": "failed", "error": "Baseline failed"})
    result = service.validate_package(db, user, session.id, "/workspace/source/draft/knapsack")
    assert result["project_ready"] is False
    with pytest.raises(HTTPException) as error:
        service.prepare_task(db, user, session.id, AutoDesignPublishRequest())
    assert error.value.status_code == 409


@pytest.mark.usefixtures("verified_package")
def test_new_design_has_no_task_until_launch_and_retries_do_not_start_twice(environment, monkeypatch):
    """Keep preparation taskless and atomically hand off the verified snapshot."""
    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate(title="Conversational design"))
    draft = paper_service.paper_workspace_root(user.id, session.id) / "source/draft/knapsack"
    draft.mkdir(parents=True)
    service.validate_package(db, user, session.id, "/workspace/source/draft/knapsack")
    assert db.exec(select(models.Task)).all() == []

    def submit(db, task_id, _user, _token):
        task = db.get(models.Task, task_id)
        task.status = models.TaskStatus.PENDING
        db.add(task)
        db.commit()

    start = Mock(side_effect=submit)
    monkeypatch.setattr(task_service, "run_task", start)
    first = service.launch_session(db, user, session.id, AutoDesignPublishRequest())
    retried = service.launch_session(db, user, session.id, AutoDesignPublishRequest())
    assert first.task.id == retried.task.id
    assert first.task.status == models.TaskStatus.PENDING
    assert service.resolve_task_session(db, user, first.task.id).id == session.id
    assert len(db.exec(select(models.Task)).all()) == 1
    start.assert_called_once()


def test_launch_requires_real_verified_package(environment, monkeypatch):
    """Never create an execution task from an unverified conversation."""
    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    start = Mock()
    monkeypatch.setattr(task_service, "run_task", start)
    with pytest.raises(HTTPException) as error:
        service.launch_session(db, user, session.id, AutoDesignPublishRequest())
    assert error.value.status_code == 409
    assert db.exec(select(models.Task)).all() == []
    start.assert_not_called()


def test_analysis_is_scoped_read_only_and_available_without_a_task(environment):
    """Browse design records and real timestamps before any runnable task exists."""
    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    root = paper_service.paper_workspace_root(user.id, session.id) / "source"
    assert service.list_files(db, user, session.id, "analysis").files == []
    with pytest.raises(HTTPException) as missing:
        service.read_file(db, user, session.id, "analysis", "missing.md")
    assert missing.value.status_code == 404
    record = write_file(root, "analysis/design.md", "# Design decisions\nKeep the evaluator deterministic.\n")
    os.utime(record, (1_700_000_000, 1_700_000_000))
    write_file(root, "analysis/verification/run-1/report.json", '{"status": "passed"}\n')
    write_file(root, "draft/candidate.py", "CANDIDATE = 1\n")
    write_file(root, "references/brief.md", "# Requirements\n")
    for hidden in (".env", ".env.local", ".npmrc", ".pypirc", ".git/config", ".venv/config"):
        write_file(root / "analysis", hidden, "must stay hidden")
        with pytest.raises(HTTPException) as secret:
            service.read_file(db, user, session.id, "analysis", hidden)
        assert secret.value.status_code == 403

    listing = service.list_files(db, user, session.id, "analysis")
    assert listing.area == "analysis"
    assert listing.task_id is None
    assert [file.path for file in listing.files] == ["design.md", "verification/run-1/report.json"]
    assert listing.files[0].size == record.stat().st_size
    assert listing.files[0].modified_time == datetime.fromtimestamp(record.stat().st_mtime, tz=UTC)
    assert listing.model_dump(mode="json")["files"][0]["modified_time"] == "2023-11-14T22:13:20Z"
    subtree = service.list_files(db, user, session.id, "analysis", directory="verification")
    assert [file.path for file in subtree.files] == ["verification/run-1/report.json"]
    loaded = service.read_file(db, user, session.id, "analysis", "design.md")
    assert loaded.content == record.read_text()
    assert loaded.revision == hashlib.sha256(record.read_bytes()).hexdigest()
    assert loaded.editable is False
    assert db.exec(select(models.Task)).all() == []


@pytest.mark.parametrize("operation", ["list_files", "read_file"])
def test_taskless_analysis_rejects_foreign_owners_and_escaping_root_links(environment, operation):
    """Authorize the session before resolving records or a redirected area root."""
    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    foreign = service.create_session(db, environment.other, AutoDesignSessionCreate())
    root = paper_service.paper_workspace_root(user.id, session.id) / "source"
    foreign_root = paper_service.paper_workspace_root(environment.other.id, foreign.id) / "source/analysis"
    write_file(foreign_root, "private.md", "Foreign design records")
    request = AutoDesignOperationRequest(operation=operation, arguments={"area": "analysis", "path": "private.md"})
    with pytest.raises(HTTPException) as owner_error:
        service.operate(db, environment.other, session.id, request)
    assert owner_error.value.status_code == 404
    with pytest.raises(HTTPException) as foreign_error:
        service.operate(db, user, foreign.id, request)
    assert foreign_error.value.status_code == 404
    (root / "analysis").symlink_to(foreign_root, target_is_directory=True)
    with pytest.raises(HTTPException) as link_error:
        service.operate(db, user, session.id, request)
    assert link_error.value.status_code == 400


@pytest.mark.parametrize("area", ["draft", "references", "analysis", "run", "input"])
def test_file_listing_modified_time_is_optional_only_for_storage_inputs(environment, make_task, area):
    """Return UTC filesystem modification times and leave unavailable storage metadata null."""
    db, user = environment.db, environment.user
    task = make_task()
    session = service.resolve_task_session(db, user, task.id)
    root = paper_service.paper_workspace_root(user.id, session.id) / "source"
    if area == "input":
        listing = service.list_files(db, user, session.id, area)
        assert listing.files
        assert all(file.modified_time is None for file in listing.files)
    else:
        local = runtime_root(environment, task) if area == "run" else root / area
        relative = f"{task.id}/notes.md" if area == "draft" else "notes.md"
        record = write_file(local, relative, "# Timestamped file\n")
        listing = service.list_files(db, user, session.id, area)
        item = next(file for file in listing.files if file.path == relative)
        assert item.modified_time == datetime.fromtimestamp(record.stat().st_mtime, tz=UTC)
        assert item.title == ("Timestamped file" if area == "analysis" else None)


def test_analysis_titles_preserve_document_names_without_exposing_credentials(environment):
    """Expose readable headings while applying the same redaction as full reads."""
    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    root = paper_service.paper_workspace_root(user.id, session.id) / "source/analysis"
    write_file(root, "options.md", "\ufeff# 初始算法选择说明\n\nComplete document stays intact.\n")
    write_file(root, "secret.md", "# api_key: sk-title-credential-must-stay-hidden\n")
    write_file(root, "plain.md", "No heading here.\n")
    listing = {file.path: file for file in service.list_files(db, user, session.id, "analysis").files}
    assert listing["options.md"].title == "初始算法选择说明"
    assert listing["plain.md"].title is None
    assert "sk-title-credential" not in listing["secret.md"].title


def test_analysis_http_and_runtime_reads_cannot_mutate_records(environment, monkeypatch):
    """Use existing HTTP and MCP envelopes while keeping mutations scoped to their original areas."""
    from fastapi import UploadFile
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user, get_db
    from app.main import app
    from app.services.paper_workspace_runtime import paper_workspace_runtime_token

    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    root = paper_service.paper_workspace_root(user.id, session.id) / "source"
    record = write_file(root, "analysis/decisions.md", "# Original design record\n")
    write_file(root, "analysis/config.yaml", "api_key: never-expose-this\n")
    binary = root / "analysis/binary.bin"
    binary.write_bytes(b"\x00\xff")
    (root / "draft").mkdir(exist_ok=True)
    (root / "draft/record.md").symlink_to(record)
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: user)
    base = f"/api/v1/llm4ad/autodesign/sessions/{session.id}"
    client = TestClient(app)
    try:
        listing = client.get(base + "/files", params={"area": "analysis"})
        assert listing.status_code == 200
        assert listing.json()["area"] == "analysis"
        assert listing.json()["task_id"] is None
        assert all(file["modified_time"] for file in listing.json()["files"])
        response = client.get(base + "/file", params={"area": "analysis", "path": "decisions.md"})
        assert response.status_code == 200
        loaded = response.json()
        assert loaded["content"] == record.read_text()
        assert loaded["editable"] is False
        redacted = client.get(base + "/file", params={"area": "analysis", "path": "config.yaml"})
        assert redacted.status_code == 200
        assert "never-expose-this" not in redacted.json()["content"]
        assert client.get(base + "/file", params={"area": "analysis", "path": "binary.bin"}).status_code == 400
        envelope = {"operation": "read_file", "arguments": {"area": "analysis", "path": "decisions.md"}}
        assert client.post(base + "/operations", json=envelope).json() == loaded
        runtime = client.post(
            "/api/v1/llm4ad/autodesign/runtime/operations",
            json={**envelope, "workspace_id": str(session.id)},
            headers={"X-LLM4AD-Runtime-Token": paper_workspace_runtime_token(session.id)},
        )
        assert runtime.status_code == 200
        assert runtime.json() == loaded
        for path in ("decisions.md", "../analysis/decisions.md", "record.md"):
            update = {"path": path, "content": "changed", "revision": loaded["revision"]}
            assert client.put(base + "/file", params={"area": "analysis"}, json=update).status_code in (400, 404)
        assert client.delete(base + "/materials", params={"path": "../analysis/decisions.md"}).status_code == 400
    finally:
        client.close()
    with pytest.raises(HTTPException):
        service.upload_materials(db, user, session.id, [UploadFile(filename="../analysis/decisions.md", file=io.BytesIO(b"changed"))])
    assert record.read_text() == loaded["content"]
    assert db.exec(select(models.Task)).all() == []


@pytest.mark.usefixtures("verified_package")
def test_analysis_records_never_enter_prepared_task_inputs(environment, entries):
    """Prepare only the verified draft even when analysis contains project-shaped files."""
    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    root = paper_service.paper_workspace_root(user.id, session.id) / "source"
    record = write_file(root, "analysis/design.md", "# Design record outside runnable inputs\n")
    write_file(root, "analysis/config.yaml", "project_name: Design records\n")
    (root / "draft/knapsack").mkdir(parents=True)
    service.validate_package(db, user, session.id, "/workspace/source/draft/knapsack")
    assert service.list_files(db, user, session.id, "analysis").files

    prepared = service.prepare_task(db, user, session.id, AutoDesignPublishRequest())
    task = db.get(models.Task, prepared.task.id)
    inputs = service._input_entries(task)
    assert set(inputs) == set(entries)
    assert record.read_bytes() not in inputs.values()
    assert service.read_file(db, user, session.id, "analysis", "design.md").editable is False
    assert "analysis" not in {key.split("/", 1)[0] for key in inputs}


def test_references_are_readable_without_a_task_and_do_not_replace_drafts(environment):
    """Make optional uploaded requirements available to the same Agent workspace."""
    from fastapi import UploadFile

    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    root = paper_service.paper_workspace_root(user.id, session.id) / "source"
    write_file(root, "draft/candidate.py", "CANDIDATE = 1\n")
    result = service.upload_materials(
        db, user, session.id, [UploadFile(filename="requirements.md", file=io.BytesIO(b"# Actual objective\n"))]
    )
    assert [file.path for file in result.files] == ["requirements.md"]
    assert service.read_file(db, user, session.id, "references", "requirements.md").content == "# Actual objective\n"
    assert (root / "draft/candidate.py").read_text() == "CANDIDATE = 1\n"
    assert db.exec(select(models.Task)).all() == []
    with pytest.raises(HTTPException):
        service.upload_materials(db, environment.other, session.id, [])


@pytest.mark.parametrize("path", ["../draft/candidate.py", "/tmp/reference.txt", "nested/../../escape"])
def test_reference_upload_rejects_paths_outside_reference_area(environment, path):
    """A client filename cannot overwrite an Agent draft or an unrelated file."""
    from fastapi import UploadFile

    session = service.create_session(environment.db, environment.user, AutoDesignSessionCreate())
    with pytest.raises(HTTPException):
        service.upload_materials(
            environment.db, environment.user, session.id, [UploadFile(filename=path, file=io.BytesIO(b"x"))]
        )


def test_materials_api_accepts_multipart_and_launch_requires_validation(environment, monkeypatch):
    """Exercise the HTTP contract used by reference uploads and the Start button."""
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user, get_db
    from app.main import app

    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: user)
    client = TestClient(app)
    base = f"/api/v1/llm4ad/autodesign/sessions/{session.id}"
    uploaded = client.post(
        base + "/materials", files=[("files", ("requirements.md", b"# Run contract\n", "text/markdown"))]
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["files"][0]["path"] == "requirements.md"
    content = client.get(base + "/file", params={"area": "references", "path": "requirements.md"})
    assert content.status_code == 200
    assert content.json()["content"] == "# Run contract\n"
    deleted = client.delete(base + "/materials", params={"path": "requirements.md"})
    assert deleted.status_code == 200
    assert deleted.json()["files"] == []
    assert client.post(base + "/launch", json={}).status_code == 409
    assert db.exec(select(models.Task)).all() == []


def test_new_design_and_seeded_draft_are_writable_by_runtime_user(environment, make_task):
    """Root-created workspaces and task seeds remain editable as UID 65534."""
    session = service.create_session(environment.db, environment.user, AutoDesignSessionCreate())
    root = paper_service.paper_workspace_root(environment.user.id, session.id)
    assert root.stat().st_mode & 0o002
    assert (root / 'source').stat().st_mode & 0o002
    task = make_task()
    bound = service.resolve_task_session(environment.db, environment.user, task.id)
    draft = paper_service.paper_workspace_root(environment.user.id, bound.id) / 'source/draft' / str(task.id)
    assert draft.stat().st_mode & 0o002
    assert (draft / 'algorithm/solver.py').stat().st_mode & 0o002


@pytest.mark.parametrize(("name", "content"), [("fake.txt", b"%PDF-1.7"), ("paper.pdf", b"%PDF-1.7"), ("bundle.zip", b"PK00"), ("fake.md", b"hello\x00binary"), ("invalid.txt", b"\xff\xfe"), ("script.html", b"<script>alert(1)</script>")])
def test_reference_documents_reject_binary_and_active_formats(environment, name, content):
    """Filename and actual UTF-8 content must both identify a text document."""
    from fastapi import UploadFile
    session = service.create_session(environment.db, environment.user, AutoDesignSessionCreate())
    with pytest.raises(HTTPException) as error:
        service.upload_materials(environment.db, environment.user, session.id, [UploadFile(filename=name, file=io.BytesIO(content))])
    assert error.value.status_code == 415
    assert service.list_files(environment.db, environment.user, session.id, "references").files == []


def test_reference_delete_is_owned_and_cannot_remove_drafts(environment):
    """Delete a reference directly while preserving independent generated source."""
    from fastapi import UploadFile
    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    service.upload_materials(db, user, session.id, [UploadFile(filename="brief.md", file=io.BytesIO(b"# Requirements"))])
    root = paper_service.paper_workspace_root(user.id, session.id) / "source"
    write_file(root, "draft/algorithm.py", "candidate = 1")
    with pytest.raises(HTTPException):
        service.delete_material(db, environment.other, session.id, "brief.md")
    with pytest.raises(HTTPException):
        service.delete_material(db, user, session.id, "../draft/algorithm.py")
    assert service.delete_material(db, user, session.id, "brief.md").files == []
    assert (root / "draft/algorithm.py").read_text() == "candidate = 1"


@pytest.mark.usefixtures("verified_package")
def test_changed_draft_invalidates_start_and_previews_actual_parameters(environment):
    """A successful old snapshot cannot authorize different displayed parameters."""
    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    root = paper_service.paper_workspace_root(user.id, session.id) / "source/draft/knapsack"
    root.mkdir(parents=True)
    service.validate_package(db, user, session.id, "/workspace/source/draft/knapsack")
    original = service.get_context(db, user, session.id)
    assert original.validation_current
    assert original.projects[0].config["evolution"]["max_generations"] == 5
    config = yaml.safe_load((root / "config.yaml").read_text())
    config["evolution"]["max_generations"] = 17
    (root / "config.yaml").write_text(yaml.safe_dump(config))
    updated = service.get_context(db, user, session.id)
    assert not updated.validation_current
    assert updated.projects[0].config["evolution"]["max_generations"] == 17
    with pytest.raises(HTTPException) as error:
        service.launch_session(db, user, session.id, AutoDesignPublishRequest())
    assert error.value.status_code == 409
    assert db.exec(select(models.Task)).all() == []


@pytest.mark.usefixtures("verified_package")
def test_draft_editor_expires_validation_and_requires_a_matching_revision(environment):
    """User edits reach the shared draft without publishing stale verified inputs."""
    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    root = paper_service.paper_workspace_root(user.id, session.id) / "source/draft/knapsack"
    root.mkdir(parents=True)
    service.validate_package(db, user, session.id, "/workspace/source/draft/knapsack")
    write_file(root, "notes.md", "# Original\n")
    loaded = service.read_file(db, user, session.id, "draft", "knapsack/notes.md")
    assert loaded.editable
    request = AutoDesignFileUpdate(path=loaded.path, content="# Edited\n", revision=loaded.revision)
    saved = service.update_draft_file(db, user, session.id, request)
    assert saved.content == "# Edited\n"
    assert saved.revision != loaded.revision
    assert (root / "notes.md").read_text() == saved.content
    assert not service.get_context(db, user, session.id).validation_current
    assert not service.get_session(db, user, session.id).project_ready
    with pytest.raises(HTTPException) as stale:
        service.update_draft_file(db, user, session.id, request)
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException):
        service.launch_session(db, user, session.id, AutoDesignPublishRequest())
    assert db.exec(select(models.Task)).all() == []


def test_draft_edits_respect_ownership_path_scope_and_active_validation(environment):
    """Reject stale writes into other workspaces, symlinks, or validating drafts."""
    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    root = paper_service.paper_workspace_root(user.id, session.id) / "source"
    write_file(root, "draft/notes.md", "original")
    write_file(root, "references/brief.md", "reference")
    loaded = service.read_file(db, user, session.id, "draft", "notes.md")
    request = AutoDesignFileUpdate(path=loaded.path, content="edited", revision=loaded.revision)
    for actor, path in [(environment.other, "notes.md"), (user, "../references/brief.md")]:
        with pytest.raises(HTTPException):
            service.update_draft_file(db, actor, session.id, request.model_copy(update={"path": path}))
    (root / "draft/link.md").symlink_to(root / "references/brief.md")
    with pytest.raises(HTTPException):
        service.update_draft_file(db, user, session.id, request.model_copy(update={"path": "link.md"}))
    association = db.get(models.AutoDesignSession, session.id)
    association.validation_report = {"status": "running"}
    db.add(association)
    db.commit()
    with pytest.raises(HTTPException) as validating:
        service.update_draft_file(db, user, session.id, request)
    assert validating.value.status_code == 409
    assert (root / "draft/notes.md").read_text() == "original"


@pytest.mark.parametrize("status", [models.TaskStatus.PENDING, models.TaskStatus.RUNNING])
def test_running_task_locks_agent_mutations_and_reference_changes(environment, make_task, status):
    """Stale pages and MCP calls cannot mutate an active execution family."""
    db, user = environment.db, environment.user
    task = make_task()
    session = service.resolve_task_session(db, user, task.id)
    task.status = status
    db.add(task)
    db.commit()
    assert service.get_context(db, user, session.id).editing_locked
    actions = [lambda: service.upload_materials(db, user, session.id, []),
               lambda: service.delete_material(db, user, session.id, "brief.md"),
               lambda: service.create_child(db, user, session.id),
               lambda: service.validate_package(db, user, session.id, "/workspace/source/draft"),
               lambda: service.ensure_session_editable(db, user, session.id)]
    for action in actions:
        with pytest.raises(HTTPException) as error:
            action()
        assert error.value.status_code == 409


def test_invalid_reference_batch_does_not_replace_existing_files(environment):
    """Validate the entire upload before changing any existing reference."""
    from fastapi import UploadFile
    db, user = environment.db, environment.user
    session = service.create_session(db, user, AutoDesignSessionCreate())
    service.upload_materials(db, user, session.id, [UploadFile(filename="brief.md", file=io.BytesIO(b"original"))])
    with pytest.raises(HTTPException):
        service.upload_materials(db, user, session.id, [UploadFile(filename="brief.md", file=io.BytesIO(b"replacement")), UploadFile(filename="bad.zip", file=io.BytesIO(b"PK00"))])
    assert service.read_file(db, user, session.id, "references", "brief.md").content == "original"


def test_agent_memory_scope_uses_selected_family_and_existing_card_service(environment, make_task, monkeypatch):
    """Read family memory through the same scoped service as the web manager."""
    from app.schemas.memory import MemoryCardPageResponse, MemoryCardResponse

    root = make_task()
    project = environment.db.get(models.Project, root.project_id)
    child = make_task(project=project, parent=root)
    session = service.resolve_task_session(environment.db, environment.user, child.id)
    listing = Mock(return_value=MemoryCardPageResponse(items=[MemoryCardResponse(id="experience", type="error_reflection", title="Observed failure", content="See the actual log")], page=2, page_size=10, total=21, has_more=True))
    monkeypatch.setattr(service.memory_service, "list_memory_cards_page", listing)

    result = service.operate(environment.db, environment.user, session.id, AutoDesignOperationRequest(
        operation="memory_list", task_id=root.id, arguments={"scope": "task", "page": 2, "page_size": 10},
    ))

    assert result["items"][0]["id"] == "experience"
    assert result["has_more"] is True
    listing.assert_called_once_with(environment.db, environment.user, scope="task", project_id=root.project_id, task_id=root.id, page=2, page_size=10)


@pytest.mark.parametrize("scope", ["user", "project", "task"])
def test_agent_memory_rejects_unrelated_task_before_remote_access(environment, make_task, monkeypatch, scope):
    """Never let an otherwise owned task escape this design's family."""
    root, unrelated = make_task(), make_task()
    session = service.resolve_task_session(environment.db, environment.user, root.id)
    listing = Mock()
    monkeypatch.setattr(service.memory_service, "list_memory_cards_page", listing)
    with pytest.raises(HTTPException) as error:
        service.operate(environment.db, environment.user, session.id, AutoDesignOperationRequest(
            operation="memory_list", task_id=unrelated.id, arguments={"scope": scope},
        ))
    assert error.value.status_code == 404
    listing.assert_not_called()


def test_taskless_agent_can_use_user_memory_without_creating_a_task(environment, monkeypatch):
    """Independent construction can consult the account's existing cards."""
    from app.schemas.memory import MemoryCardPageResponse

    session = service.create_session(environment.db, environment.user, AutoDesignSessionCreate())
    listing = Mock(return_value=MemoryCardPageResponse(items=[], page=1, page_size=20, total=0, has_more=False))
    monkeypatch.setattr(service.memory_service, "list_memory_cards_page", listing)
    service.operate(environment.db, environment.user, session.id, AutoDesignOperationRequest(operation="memory_list", arguments={"scope": "user"}))
    listing.assert_called_once_with(environment.db, environment.user, scope="user", project_id=None, task_id=None, page=1, page_size=20)
    assert not environment.db.exec(select(models.Task)).all()
    with pytest.raises(HTTPException) as error:
        service.operate(environment.db, environment.user, session.id, AutoDesignOperationRequest(operation="memory_list", arguments={"scope": "task"}))
    assert error.value.status_code == 409


def test_agent_saves_verified_memory_through_existing_card_service(environment, make_task, monkeypatch):
    """Keep Agent writes and manually managed cards in the same store."""
    from app.schemas.memory import MemoryCardResponse

    task = make_task()
    session = service.resolve_task_session(environment.db, environment.user, task.id)
    save = Mock(return_value=MemoryCardResponse(id="saved", type="error_reflection", title="Failure", content="Evidence from logs"))
    monkeypatch.setattr(service.memory_service, "upsert_memory_card", save)
    result = service.operate(environment.db, environment.user, session.id, AutoDesignOperationRequest(
        operation="memory_save", arguments={"scope": "task", "card": {"type": "error_reflection", "title": "Failure", "content": "Evidence from logs", "metadata": {"source_task_id": str(task.id), "source_path": "llm4ad/run/logs/llm4ad.log"}}},
    ))
    assert result["id"] == "saved"
    assert save.call_args.kwargs["task_id"] == task.id
    assert save.call_args.kwargs["request"].metadata["source_task_id"] == str(task.id)


def test_agent_memory_context_reports_defaults_without_enabling_evolution(environment, make_task, monkeypatch):
    """Expose actual readiness and scopes without changing the task's choices."""
    root = make_task()
    child = make_task(project=environment.db.get(models.Project, root.project_id), parent=root)
    session = service.resolve_task_session(environment.db, environment.user, child.id)
    before = copy.deepcopy(child.input_args)
    config = SimpleNamespace(system_runtime_available=True, mindmemos_binding_id="bound", model_dump=lambda **_: {"enabled": False, "api_key": "must-stay-private"})
    monkeypatch.setattr(service.memory_service, "get_project_memory_config", Mock(return_value=config))
    result = service.operate(environment.db, environment.user, session.id, AutoDesignOperationRequest(operation="memory_context"))
    assert result["available"] is True
    assert result["scopes"] == ["user", "project", "task"]
    assert result["task_id"] == str(child.id)
    assert result["root_task_id"] == str(root.id)
    assert result["config"]["api_key"] == "[redacted]"
    assert child.input_args == before


@pytest.mark.parametrize("memory_id", [None, "existing"])
def test_memory_card_evidence_survives_existing_service_payload(environment, monkeypatch, memory_id):
    """Persist Agent provenance while retaining canonical platform metadata."""
    from app.schemas.memory import MemoryCardUpsertRequest

    requests = []
    def post(_user, path, payload, **_kwargs):
        requests.append((path, payload))
        return {"data": {"memories": []}}
    monkeypatch.setattr(service.memory_service, "_mindmemos_post", post)
    service.memory_service._remote_upsert_card(environment.user, {"user_id": "owned", "app_id": "llm4ad", "session_id": "global", "agent_id": "global"}, "user", MemoryCardUpsertRequest(
        id=memory_id, content="Observed failure at the cited path", metadata={"llm4ad_evidence": {"source_task_id": "selected", "source_path": "logs/llm4ad.log"}, "source": "wrong-owner"},
    ))
    payload = requests[0][1]
    metadata = payload["metadata_patch" if memory_id else "metadata"]
    assert metadata["llm4ad_evidence"]["source_task_id"] == "selected"
    assert metadata["llm4ad_evidence"]["source_path"] == "logs/llm4ad.log"
    assert metadata["source"] == "llm4ad"


@pytest.mark.parametrize("status", [models.TaskStatus.COMPLETED, models.TaskStatus.FAILED, models.TaskStatus.RUNNING])
def test_executed_task_parameters_remain_attached_to_original_results(environment, make_task, status):
    """Reject historical input changes without changing result provenance."""
    task = make_task()
    task.status = status
    environment.db.add(task)
    environment.db.commit()
    before = copy.deepcopy(task.input_args)
    with pytest.raises(HTTPException) as error:
        task_crud.update_task(environment.db, task.id, environment.user, TaskUpdate(input_args={**before, 'random_seed': 71}))
    assert error.value.status_code == 409
    assert task.input_args == before
    assert task.reports == {"analysis": {"summary": "Parent report"}}


def test_manual_child_starts_without_parent_reports_or_logs(environment, make_task):
    """The manual copy path matches the Agent's clean experiment semantics."""
    root = make_task()
    child = task_crud.copy_task(environment.db, root.id, environment.user, is_child=True)
    assert child.reports is None
    assert not environment.db.exec(select(models.TaskLog).where(models.TaskLog.task_id == child.id)).all()
    assert environment.db.exec(select(models.TaskLog).where(models.TaskLog.task_id == root.id)).all()


def test_manual_parameter_edits_update_unchanged_draft_fields(environment, make_task):
    """Read stored changes without requiring a new conversation or losing edits."""
    root = make_task()
    root.status = models.TaskStatus.UNINITIALIZED
    root.celery_task_id = None
    environment.db.add(root)
    environment.db.commit()
    session = service.resolve_task_session(environment.db, environment.user, root.id)
    workspace = environment.db.get(models.PaperWorkspace, session.id)
    draft = service._ensure_draft(workspace, root)
    draft_config = yaml.safe_load((draft / 'config.yaml').read_text())
    draft_config['random_seed'] = 22
    (draft / 'config.yaml').write_text(yaml.safe_dump(draft_config))
    changed = copy.deepcopy(root.input_args)
    changed['evolution']['max_generations'] = 41
    task_crud.update_task(environment.db, root.id, environment.user, TaskUpdate(input_args=changed))
    context = service.get_context(environment.db, environment.user, session.id)
    updated = yaml.safe_load((draft / 'config.yaml').read_text())
    assert context.config['evolution']['max_generations'] == updated['evolution']['max_generations'] == 41
    assert updated['random_seed'] == 22


@pytest.mark.usefixtures('verified_package')
def test_parent_publication_preserves_target_child_bindings_and_memory(environment, make_task):
    """A parent conversation must not replace the child's selected settings."""
    db, user = environment.db, environment.user
    root = make_task()
    root.input_args = {**root.input_args, 'coder': {**root.input_args['coder'], 'provider_model': 'parent-model'}, 'memory': {**root.input_args.get('memory', {}), 'enabled': True}}
    db.add(root)
    db.commit()
    session = service.resolve_task_session(db, user, root.id)
    result = service.create_child(db, user, session.id, config_patch={'coder': {'provider_model': 'child-model'}, 'memory': {'enabled': False, 'retrieval_mode': 'manual', 'pinned_card_ids': ['known-task-card']}})
    child = db.get(models.Task, uuid.UUID(result['task_id']))
    service.validate_project(db, user, session.id, result['draft_path'])
    service.prepare_task(db, user, session.id, AutoDesignPublishRequest(task_id=child.id))
    assert child.input_args['coder']['provider_model'] == 'child-model'
    assert child.input_args['memory']['enabled'] is False
    assert child.input_args['memory']['retrieval_mode'] == 'manual'
    assert child.input_args['memory']['pinned_card_ids'] == ['known-task-card']


@pytest.mark.parametrize('operation', ['start', 'stop', 'create_child', 'patch_child', 'sync_draft'])
def test_runtime_mutations_require_explicit_target_ids(environment, make_task, operation):
    """A conversation's old default must never silently choose a mutation target."""
    task = make_task()
    session = service.resolve_task_session(environment.db, environment.user, task.id)
    with pytest.raises(HTTPException) as error:
        service.operate(environment.db, environment.user, session.id, AutoDesignOperationRequest(operation=operation))
    assert error.value.status_code == 422
    assert task.status == models.TaskStatus.COMPLETED


@pytest.mark.parametrize('status', [models.TaskStatus.COMPLETED, models.TaskStatus.FAILED, models.TaskStatus.RUNNING, models.TaskStatus.PENDING])
def test_run_rejects_executed_version_before_clearing_any_evidence(environment, make_task, monkeypatch, status):
    """Reject every resubmission before provider calls, Redis writes or task dispatch."""
    from app.services.task_service import execution
    task = make_task()
    task.status = status
    environment.db.add(task)
    environment.db.commit()
    providers = Mock(side_effect=AssertionError('Historical version reached execution preparation'))
    monkeypatch.setattr(execution, '_resolve_providers', providers)
    with pytest.raises(HTTPException) as error:
        execution.run_task(environment.db, task.id, environment.user)
    assert error.value.status_code == 409
    providers.assert_not_called()
    assert task.reports == {'analysis': {'summary': 'Parent report'}}
    assert environment.db.exec(select(models.TaskLog).where(models.TaskLog.task_id == task.id)).all()


@pytest.mark.usefixtures("verified_package")
def test_draft_conflicts_preserve_both_values_and_allow_explicit_resolution(environment, make_task):
    """A manual edit cannot erase an Agent edit, or be overwritten at publication."""
    task = make_task()
    task.status, task.celery_task_id = models.TaskStatus.UNINITIALIZED, None
    environment.db.add(task)
    environment.db.commit()
    session = service.resolve_task_session(environment.db, environment.user, task.id)
    workspace = environment.db.get(models.PaperWorkspace, session.id)
    draft = service._ensure_draft(workspace, task, session)
    local = yaml.safe_load((draft / 'config.yaml').read_text())
    local['random_seed'] = 17
    (draft / 'config.yaml').write_text(yaml.safe_dump(local))
    canonical = copy.deepcopy(task.input_args)
    canonical['random_seed'] = 29
    task_crud.update_task(environment.db, task.id, environment.user, TaskUpdate(input_args=canonical))
    context = service.get_context(environment.db, environment.user, session.id)
    assert 'config.random_seed' in context.draft_conflicts
    assert task.input_args['random_seed'] == 29
    assert yaml.safe_load((draft / 'config.yaml').read_text())['random_seed'] == 17
    with pytest.raises(HTTPException) as error:
        service.validate_project(environment.db, environment.user, session.id, context.draft_path)
    assert error.value.status_code == 409
    resolved = service.sync_task_draft(environment.db, environment.user, session.id, task.id, 'keep_draft')
    assert resolved['conflicts'] == []
    assert task.input_args['random_seed'] == 29
    service.validate_project(environment.db, environment.user, session.id, context.draft_path)
    service.prepare_task(environment.db, environment.user, session.id, AutoDesignPublishRequest(task_id=task.id))
    assert task.input_args['random_seed'] == 17
    assert len(environment.db.exec(select(models.Task)).all()) == 1


@pytest.mark.usefixtures("verified_package")
def test_canonical_edit_expires_verification_before_any_republication(environment, make_task):
    """Ready state and preparation both reject a verified but superseded draft."""
    task = make_task()
    session = service.resolve_task_session(environment.db, environment.user, task.id)
    result = service.create_child(environment.db, environment.user, session.id)
    child = environment.db.get(models.Task, uuid.UUID(result['task_id']))
    service.validate_project(environment.db, environment.user, session.id, result['draft_path'])
    changed = copy.deepcopy(child.input_args)
    changed['random_seed'] = 33
    task_crud.update_task(environment.db, child.id, environment.user, TaskUpdate(input_args=changed))
    assert service.get_session(environment.db, environment.user, session.id).project_ready is False
    with pytest.raises(HTTPException) as error:
        service.prepare_task(environment.db, environment.user, session.id, AutoDesignPublishRequest(task_id=child.id))
    assert error.value.status_code == 409
    assert child.input_args['random_seed'] == 33


def test_historical_canonical_files_are_readable_but_not_editable(environment, make_task):
    """Keep source evidence readable while preventing writes before storage access."""
    from app.schemas.task import FileUpdateRequest
    task = make_task()
    before = dict(environment.storage.files)
    with pytest.raises(HTTPException) as error:
        task_files.update_task_data_file(environment.db, task.id, environment.user, FileUpdateRequest(file_path='knapsack/algorithm/solver.py', content='changed'))
    assert error.value.status_code == 409
    assert environment.storage.files == before


def test_selected_unstarted_root_can_be_adjusted_without_creating_a_child(environment, make_task):
    """A task that has never executed is already a valid editable target."""
    task = make_task()
    task.status, task.celery_task_id = models.TaskStatus.UNINITIALIZED, None
    environment.db.add(task)
    environment.db.commit()
    session = service.resolve_task_session(environment.db, environment.user, task.id)
    result = service.operate(environment.db, environment.user, session.id, AutoDesignOperationRequest(operation='patch_child', task_id=task.id, arguments={'config_patch': {'random_seed': 19}}))
    assert result['task_id'] == str(task.id)
    assert task.input_args['random_seed'] == 19
    assert len(environment.db.exec(select(models.Task)).all()) == 1


def test_manual_source_edits_refresh_untouched_agent_draft(environment, make_task):
    """Source-file APIs advance the revision used by draft synchronization."""
    from app.schemas.task import FileUpdateRequest
    task = make_task()
    task.status, task.celery_task_id = models.TaskStatus.UNINITIALIZED, None
    environment.db.add(task)
    environment.db.commit()
    session = service.resolve_task_session(environment.db, environment.user, task.id)
    workspace = environment.db.get(models.PaperWorkspace, session.id)
    draft = service._ensure_draft(workspace, task, session)
    name = next(name for name in service._input_entries(task) if name.endswith('.py'))
    old_revision = service._task_source_revision(task)
    task_files.update_task_data_file(environment.db, task.id, environment.user, FileUpdateRequest(file_path='knapsack/' + name, content='# Manual source change\n'))
    service.get_context(environment.db, environment.user, session.id)
    assert service._task_source_revision(task) != old_revision
    assert (draft / name).read_text() == '# Manual source change\n'


def test_starting_selected_child_preserves_parent_evidence_and_selects_the_running_version(environment, make_task, monkeypatch):
    """Agent starts have the same active-version handoff as manual starts."""
    from app.core import env_detect, redis
    from app.services import container_service
    from app.services.task_service import execution

    db, user = environment.db, environment.user
    parent = make_task()
    session = service.resolve_task_session(db, user, parent.id)
    result = service.create_child(db, user, session.id)
    child = db.get(models.Task, uuid.UUID(result['task_id']))
    monkeypatch.setattr(container_service, 'validate_task_container_host_path', lambda: None)
    monkeypatch.setattr(env_detect, 'get_project_home', lambda **_: str(environment.home))
    monkeypatch.setattr(execution, '_resolve_providers', lambda _db, value, *_args, **_kwargs: value)
    monkeypatch.setattr(execution, '_apply_mindmemos_runtime_config', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(redis, 'delete_task_logs', Mock())
    monkeypatch.setattr(redis, 'push_log_entry', Mock())
    submit = Mock(return_value=SimpleNamespace(id='isolated-submission'))
    monkeypatch.setattr(execution.run_evolution, 'apply_async', submit)
    started = service.operate(db, user, session.id, AutoDesignOperationRequest(operation='start', task_id=child.id))
    assert started['task_id'] == str(child.id)
    assert child.status == models.TaskStatus.PENDING
    assert parent.status == models.TaskStatus.COMPLETED
    assert parent.reports == {'analysis': {'summary': 'Parent report'}}
    assert parent.active_child_id == child.id
    assert db.exec(select(models.TaskLog).where(models.TaskLog.task_id == parent.id)).all()
    assert db.get(models.AutoDesignSession, session.id).task_id == parent.id
    submit.assert_called_once()


@pytest.mark.usefixtures('verified_package')
def test_saving_identical_form_values_keeps_current_verification(environment, make_task):
    """Opening an existing form must not invalidate the unchanged task package."""
    db, user = environment.db, environment.user
    parent = make_task()
    session = service.resolve_task_session(db, user, parent.id)
    result = service.create_child(db, user, session.id)
    child = db.get(models.Task, uuid.UUID(result['task_id']))
    service.validate_project(db, user, session.id, result['draft_path'])
    old_version = child.version_time
    task_crud.update_task(db, child.id, user, TaskUpdate(input_args=copy.deepcopy(child.input_args)))
    assert child.version_time == old_version
    assert service.get_session(db, user, session.id).project_ready is True
