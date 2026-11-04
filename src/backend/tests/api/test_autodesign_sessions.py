"""Exercise AutoDesign creation against the database's real foreign keys."""

import pytest
from sqlmodel import Session

from app import models
from app.core.config import settings
from app.core.db import engine
from app.schemas.autodesign import AutoDesignSessionCreate
from app.services import autodesign_service
from tests.utils.user import create_random_user


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="Requires PostgreSQL foreign key enforcement")
def test_create_session_persists_workspace_and_association(monkeypatch, tmp_path):
    """Create both records without relying on SQLite's disabled foreign keys."""
    monkeypatch.setattr(settings, "DOCKER_PROJECT_HOME", str(tmp_path))
    with Session(engine) as db:
        user = create_random_user(db)
        result = autodesign_service.create_session(db, user, AutoDesignSessionCreate(title="Description-only design"))
        workspace_id, user_id = result.id, user.id
    with Session(engine) as db:
        workspace = db.get(models.PaperWorkspace, workspace_id)
        association = db.get(models.AutoDesignSession, workspace_id)
        assert workspace is not None
        assert workspace.user_id == user_id
        assert workspace.mode == "autodesign"
        assert association is not None
        assert association.task_id is None
