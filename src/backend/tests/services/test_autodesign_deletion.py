"""Regress task deletion without resurrecting orphaned AutoDesign drafts."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import models
from app.api import deps
from app.api.llm4ad.autodesign import router
from app.core.db import engine
from app.services import autodesign_service, paper_service, task_service
from tests.utils.user import create_random_user


@pytest.fixture
def deletion(monkeypatch, tmp_path):
    """Keep deletion effects in an isolated database, temporary files and mocks."""
    storage = Mock()
    storage.list_objects.return_value = []
    stop = Mock(return_value=True)
    forget = Mock()
    revoke = Mock()
    monkeypatch.setattr(paper_service, "_storage", lambda: storage)
    monkeypatch.setattr("app.services.task_service.crud.storage", storage)
    monkeypatch.setattr("app.services.paper_workspace_runtime.stop_paper_workspace_container", stop)
    monkeypatch.setattr(paper_service, "forget_paper_workspace_active", forget)
    monkeypatch.setattr("app.services.credential_broker.revoke_task_tokens", revoke)
    monkeypatch.setattr(paper_service.settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    with Session(engine) as db:
        user = create_random_user(db)
        project = models.Project(user_id=user.id, name="Deletion regression")
        db.add(project)
        db.commit()
        yield SimpleNamespace(
            db=db, user=user, project=project, storage=storage, stop=stop, forget=forget, revoke=revoke
        )


def _task(env, *, parent=None, status=models.TaskStatus.UNINITIALIZED):
    """Create one experiment without submitting execution."""
    task = models.Task(
        project_id=env.project.id,
        name="Deletable task",
        status=status,
        parent_id=parent.id if parent else None,
        group_id=(parent.group_id or parent.id) if parent else None,
    )
    env.db.add(task)
    env.db.commit()
    return task


def _design(env, task=None):
    """Persist an owned workspace and representative Agent reference files."""
    workspace = models.PaperWorkspace(user_id=env.user.id, mode="autodesign", title="AI construction")
    env.db.add(workspace)
    env.db.flush()
    session = models.AutoDesignSession(
        workspace_id=workspace.id, project_id=env.project.id, task_id=task.id if task else None
    )
    env.db.add(session)
    env.db.commit()
    path = paper_service.paper_workspace_root(env.user.id, workspace.id)
    (path / "source/references").mkdir(parents=True)
    (path / "source/references/spec.md").write_text("User reference", encoding="utf-8")
    (path / ".cloudcli").mkdir()
    return workspace.id, path


def test_task_delete_removes_agent_instead_of_creating_a_draft(deletion):
    """The foreign key must not turn a deleted task into a new AI draft."""
    env = deletion
    task = _task(env)
    workspace_id, path = _design(env, task)
    run = models.PaperAgentRun(workspace_id=workspace_id, run_kind="autodesign", status="ready")
    env.db.add(run)
    env.db.commit()
    run_id = run.id

    task_service.delete_task(env.db, task.id, env.user)
    env.db.expire_all()

    assert autodesign_service.list_sessions(env.db, env.user, env.project.id).items == []
    assert env.db.get(models.AutoDesignSession, workspace_id) is None
    assert env.db.get(models.PaperWorkspace, workspace_id) is None
    assert not path.exists()
    env.stop.assert_called_once_with(workspace_id, remove=True)
    env.forget.assert_called_once_with(workspace_id)
    env.revoke.assert_called_once_with(run_id)


def test_root_deletion_removes_family_agents_and_versions(deletion):
    """Deleting the task-list root must not leave invisible child workspaces."""
    env = deletion
    root = _task(env)
    child = _task(env, parent=root)
    ids = [root.id, child.id]
    workspaces = [_design(env, task) for task in [root, child]]

    task_service.delete_task(env.db, root.id, env.user)
    env.db.expire_all()

    assert all(env.db.get(models.Task, task_id) is None for task_id in ids)
    assert all(not path.exists() for _, path in workspaces)
    assert autodesign_service.list_sessions(env.db, env.user, env.project.id).items == []


def test_child_deletion_keeps_parent_conversation_and_other_versions(deletion):
    """Publishing a child from the parent does not transfer conversation ownership."""
    env = deletion
    root = _task(env)
    child = _task(env, parent=root)
    sibling = _task(env, parent=root)
    parent_workspace, parent_path = _design(env, root)
    child_workspace, child_path = _design(env, child)
    session = env.db.get(models.AutoDesignSession, parent_workspace)
    session.published_task_id = child.id
    root.active_child_id = child.id
    env.db.add(root)
    env.db.add(session)
    env.db.commit()

    task_service.delete_task(env.db, child.id, env.user)
    env.db.expire_all()

    assert env.db.get(models.Task, root.id) is not None
    assert env.db.get(models.Task, root.id).active_child_id == root.id
    assert env.db.get(models.Task, sibling.id) is not None
    assert env.db.get(models.AutoDesignSession, parent_workspace) is not None
    assert parent_path.exists()
    assert not child_path.exists()
    env.stop.assert_called_once_with(child_workspace, remove=True)


def test_running_child_blocks_root_deletion_before_agent_cleanup(deletion):
    """A historical root cannot hide a still-running child from the delete guard."""
    env = deletion
    root = _task(env)
    child = _task(env, parent=root, status=models.TaskStatus.RUNNING)
    workspace_id, path = _design(env, root)

    with pytest.raises(HTTPException) as exc:
        task_service.delete_task(env.db, root.id, env.user)

    assert exc.value.status_code == 409
    assert path.exists()
    assert env.db.get(models.AutoDesignSession, workspace_id) is not None
    assert env.db.get(models.Task, child.id) is not None
    env.stop.assert_not_called()


def test_delete_unpublished_draft_also_destroys_its_agent(deletion):
    """A design discarded before task setup has its own authenticated delete path."""
    env = deletion
    workspace_id, path = _design(env)

    autodesign_service.delete_session(env.db, env.user, workspace_id)
    env.db.expire_all()

    assert env.db.get(models.AutoDesignSession, workspace_id) is None
    assert not path.exists()
    env.stop.assert_called_once_with(workspace_id, remove=True)
    assert autodesign_service.list_sessions(env.db, env.user, env.project.id).items == []


def test_draft_delete_cannot_remove_another_users_workspace(deletion):
    """Keep owner checks in force for the new delete operation."""
    env = deletion
    workspace_id, path = _design(env)
    other = create_random_user(env.db)

    with pytest.raises(HTTPException) as exc:
        autodesign_service.delete_session(env.db, other, workspace_id)

    assert exc.value.status_code == 404
    assert path.exists()
    env.stop.assert_not_called()


def test_cleanup_failure_does_not_restore_a_deleted_task(deletion):
    """Record storage cleanup failures while still removing Agent runtime files."""
    env = deletion
    task = _task(env)
    workspace_id, path = _design(env, task)
    env.storage.list_objects.side_effect = RuntimeError("Object storage unavailable")

    task_service.delete_task(env.db, task.id, env.user)
    env.db.expire_all()

    assert env.db.get(models.AutoDesignSession, workspace_id) is None
    assert not path.exists()
    env.stop.assert_called_once_with(workspace_id, remove=True)
    job = env.db.exec(select(models.PaperCleanupJob).where(models.PaperCleanupJob.user_id == env.user.id)).one()
    assert job.status == "pending"
    assert "Object storage unavailable" in job.error


def test_deleting_conversation_keeps_its_published_task(deletion):
    """Explicit conversation deletion does not erase a separately managed task."""
    env = deletion
    task = _task(env)
    workspace_id, _ = _design(env, task)

    autodesign_service.delete_session(env.db, env.user, workspace_id)
    env.db.expire_all()

    assert env.db.get(models.Task, task.id) is not None
    assert env.db.get(models.AutoDesignSession, workspace_id) is None


def test_superuser_deletion_cleans_the_workspace_owners_files(deletion):
    """Administrative task access must not change the runtime cleanup owner."""
    env = deletion
    task = _task(env)
    workspace_id, path = _design(env, task)
    admin = create_random_user(env.db)
    admin.is_superuser = True
    env.db.add(admin)
    env.db.commit()

    task_service.delete_task(env.db, task.id, admin)

    assert not path.exists()
    env.storage.list_objects.assert_called_once_with(prefix=f"paper/{env.user.id}/{workspace_id}/")


def test_task_delete_rejects_other_users_before_cleanup(deletion):
    """Task deletion cannot use Agent cleanup to cross project ownership."""
    env = deletion
    task = _task(env)
    workspace_id, path = _design(env, task)
    other = create_random_user(env.db)

    with pytest.raises(HTTPException) as exc:
        task_service.delete_task(env.db, task.id, other)

    assert exc.value.status_code == 403
    assert path.exists()
    assert env.db.get(models.AutoDesignSession, workspace_id) is not None
    env.stop.assert_not_called()


def test_deleted_draft_returns_204_and_cannot_be_reloaded(deletion):
    """Exercise the actual delete route and refreshed session-list response."""
    env = deletion
    workspace_id, path = _design(env)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/llm4ad")
    app.dependency_overrides[deps.get_db] = lambda: env.db
    app.dependency_overrides[deps.get_current_user] = lambda: env.user
    with TestClient(app) as client:
        url = f"/api/v1/llm4ad/autodesign/sessions/{workspace_id}"
        response = client.delete(url)
        assert response.status_code == 204
        assert response.content == b""
        assert client.get(url).status_code == 404
        listed = client.get("/api/v1/llm4ad/autodesign/sessions", params={"project_id": str(env.project.id)})
        assert listed.status_code == 200
        assert listed.json()["items"] == []
        assert not path.exists()


def test_failed_task_transaction_preserves_its_agent(deletion, monkeypatch):
    """External resources are removed only after task and workspace commit together."""
    env = deletion
    task = _task(env)
    task_id = task.id
    workspace_id, path = _design(env, task)
    monkeypatch.setattr(env.db, "commit", Mock(side_effect=RuntimeError("Commit failed")))

    with pytest.raises(RuntimeError, match="Commit failed"):
        task_service.delete_task(env.db, task_id, env.user)
    env.db.rollback()

    assert env.db.get(models.Task, task_id) is not None
    assert env.db.get(models.AutoDesignSession, workspace_id) is not None
    assert path.exists()
    env.stop.assert_not_called()
    env.storage.delete_many.assert_not_called()
