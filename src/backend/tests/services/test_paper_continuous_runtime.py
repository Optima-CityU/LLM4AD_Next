"""Exercise persistence and source guards through the real continuous-runtime API."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app import models
from app.api import deps
from app.api.llm4ad import paper_runtime


@pytest.fixture
def continuous_api(tmp_path, monkeypatch):
    """Create an owned runtime with an older generation base and a newer source."""
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime.sqlite'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine, tables=[item.__table__ for item in (
        models.User, models.PaperWorkspace, models.PaperSourceVersion, models.PaperAgentRun,
    )])
    monkeypatch.setattr(paper_runtime, "engine", engine)
    user = models.User(email="continuous@example.com", hashed_password="unused")
    with Session(engine) as db:
        workspace = models.PaperWorkspace(user_id=user.id, title="Continuous", mode="proposal")
        db.add(workspace)
        db.flush()
        sources = [models.PaperSourceVersion(
            workspace_id=workspace.id, version=index, source_kind="markdown", filename="proposal.typ",
            object_key=f"source-{index}", content_hash=f"hash-{index}", content_size=1,
        ) for index in (1, 2)]
        db.add_all(sources)
        db.flush()
        workspace.active_source_version_id = sources[1].id
        run = models.PaperAgentRun(
            id=paper_runtime._native_run_id(workspace.id, "workflow"), workspace_id=workspace.id,
            source_version_id=sources[0].id, run_kind="research_workflow", session_id="continuous-session",
            request_payload={"auto_workflow": True, "native_runtime": "cloudcli", "source_content_hash": "hash-1"},
        )
        db.add_all([workspace, run])
        db.commit()
        workspace_id, run_id, source_id = workspace.id, run.id, sources[1].id
    app = FastAPI()
    app.include_router(paper_runtime.router)

    def session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[deps.get_db] = session
    app.dependency_overrides[deps.get_current_user] = lambda: user
    with TestClient(app) as client:
        yield client, engine, user, workspace_id, run_id, source_id
    engine.dispose()


def snapshot():
    """Return one observed tool attempt, with no planned future steps."""
    return {
        "sessionId": "continuous-session", "status": "running", "currentTool": "Read",
        "agents": [{"id": "call-1", "parentId": "main", "stage": "methods", "description": "Read evidence",
                    "status": "running", "toolCount": 1, "currentTool": "Read",
                    "tools": [{"id": "read-1", "name": "Read", "done": False, "failed": False}]}],
    }


def test_observed_progress_survives_reload_and_rejects_wrong_sessions(continuous_api):
    """Persist actual events while refusing unrelated sessions and future states."""
    client, _engine, _user, workspace_id, _run_id, _source_id = continuous_api
    url = f"/papers/workspaces/{workspace_id}/runtime-progress"
    assert client.get(url).json() == {"progress": None}
    saved = client.post(url, json=snapshot())
    assert saved.status_code == 200
    assert saved.json()["progress"]["sequence"] == 1
    assert client.get(url).json() == saved.json()
    other = {**snapshot(), "sessionId": "foreign-session"}
    assert client.post(url, json=other).status_code == 409
    future = snapshot()
    future["agents"][0]["status"] = "pending"
    assert client.post(url, json=future).status_code == 422
    assert client.get(f"/papers/workspaces/{uuid.uuid4()}/runtime-progress").status_code == 404


def test_publication_preserves_generation_base_and_pins_fresh_explicit_base(continuous_api, monkeypatch):
    """Never silently rebase an old agent change onto newer author text."""
    client, engine, _user, workspace_id, run_id, source_id = continuous_api
    from app.tasks import paper_agent

    class Redis:
        """Retain publication claims and successful idempotent replies."""

        def __init__(self):
            self.values = {}

        async def get(self, key):
            return self.values.get(key)

        async def set(self, key, value, **kwargs):
            if kwargs.get("nx") and key in self.values:
                return False
            self.values[key] = value
            return True

        async def delete(self, key):
            self.values.pop(key, None)

    redis = Redis()

    async def get_redis():
        return redis

    monkeypatch.setattr(paper_runtime, "get_async_redis", get_redis)
    monkeypatch.setattr(paper_runtime, "touch_paper_workspace_active", lambda *_: None)
    published = []

    def persist(publication_id, _owner, _path):
        with Session(engine) as db:
            run = db.get(models.PaperAgentRun, publication_id)
            published.append((run.source_version_id, run.request_payload["source_content_hash"]))
        return False

    monkeypatch.setattr(paper_agent, "_persist_output", persist)
    body = {"workspace_id": str(workspace_id), "run_id": str(run_id), "workflow_stage": "methods",
            "idempotency_key": "revision-1", "artifact": {}}
    headers = {"x-llm4ad-runtime-token": paper_runtime.paper_workspace_runtime_token(workspace_id)}
    assert client.post("/papers/runtime/stage-results", headers=headers, json=body).status_code == 409
    assert not published
    fresh = {**body, "base_source_version_id": str(source_id), "base_content_hash": "wrong-hash"}
    assert client.post("/papers/runtime/stage-results", headers=headers, json=fresh).status_code == 409
    fresh["base_content_hash"] = "hash-2"
    first = client.post("/papers/runtime/stage-results", headers=headers, json=fresh)
    assert first.status_code == 200
    assert published == [(source_id, "hash-2")]
    assert client.post("/papers/runtime/stage-results", headers=headers, json=fresh).json() == first.json()
    assert len(published) == 1
