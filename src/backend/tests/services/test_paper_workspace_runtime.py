"""Project-scoped paper container runtime contracts."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.services import paper_workspace_runtime


class _Containers:
    """Capture Docker container creation arguments."""

    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def run(self, *_args, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(id="container-id", status="running")


def test_project_container_mounts_one_stable_owned_workspace(tmp_path) -> None:
    """Keep source and agent state in one reusable project mount."""
    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()
    containers = _Containers()
    client = SimpleNamespace(containers=containers)
    spec = paper_workspace_runtime.PaperWorkspaceExecSpec(
        workspace_id=workspace_id,
        user_id=user_id,
        host_workspace=str(tmp_path),
    )

    paper_workspace_runtime._create_workspace_container(
        spec,
        client,
        SimpleNamespace(id="image-id"),
    )

    assert containers.kwargs is not None
    assert containers.kwargs["name"] == paper_workspace_runtime.paper_workspace_container_name(workspace_id)
    assert containers.kwargs["volumes"] == {str(tmp_path.resolve()): {"bind": "/workspace", "mode": "rw"}}
    assert containers.kwargs["labels"]["llm4ad.paper-workspace-owner"] == str(user_id)
    assert containers.kwargs["command"] == ["/app/paper-agent/workspace-entrypoint.sh"]
    assert containers.kwargs["environment"] == {
        "CLAUDE_CONFIG_DIR": "/workspace/.cloudcli/.claude",
        "DATABASE_PATH": "/workspace/.cloudcli/auth.db",
        "HOME": "/workspace/.cloudcli",
        "LLM4AD_CLAUDE_CONFIG_DIR": "/workspace/.cloudcli/.claude",
        "LLM4AD_EMBEDDED_MODE": "true",
        "LLM4AD_RUNTIME_TOKEN": paper_workspace_runtime.paper_workspace_runtime_token(workspace_id),
        "LLM4AD_SKILLS_ROOT": "/app/skills",
        "LLM4AD_WORKSPACE_NAME": "Research workspace",
        "LLM4AD_WORKSPACE_ROOT": "/workspace/source",
        "SERVER_PORT": "3001",
        "VITE_IS_PLATFORM": "true",
        "WORKSPACES_ROOT": "/workspace/source",
    }
    assert containers.kwargs["healthcheck"]["test"] == [
        "CMD-SHELL",
        "node -e \"fetch('http://127.0.0.1:3001/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))\"",
    ]


def test_project_container_name_is_stable_for_the_workspace() -> None:
    """Reuse the same Docker identity across workflow stages."""
    workspace_id = uuid.uuid4()

    first = paper_workspace_runtime.paper_workspace_container_name(workspace_id)
    second = paper_workspace_runtime.paper_workspace_container_name(str(workspace_id))

    assert first == second
    assert "-" not in first.removeprefix("llm4ad-paper-workspace-")


class _Removable:
    """Record one container's image and whether it was removed."""

    def __init__(self, name: str, image: str) -> None:
        self.name = name
        self.attrs = {"Image": image}
        self.removed = False

    def remove(self, **_kwargs) -> None:
        self.removed = True


def _reconcile_with(monkeypatch, containers: list[_Removable], *, image_id: str | None) -> int:
    """Run the startup reconcile against a faked Docker client."""

    class _Images:
        def get(self, _reference: str):
            if image_id is None:
                raise paper_workspace_runtime.ImageNotFound(_reference)
            return SimpleNamespace(id=image_id)

    class _Containers:
        def list(self, **_kwargs):
            return containers

    monkeypatch.setattr(
        paper_workspace_runtime,
        "get_docker_client",
        lambda: SimpleNamespace(images=_Images(), containers=_Containers()),
    )
    return paper_workspace_runtime.reconcile_paper_workspace_containers()


def test_startup_reconcile_replaces_containers_on_a_superseded_image(monkeypatch) -> None:
    """A rebuilt image must reach workspaces nobody has reopened yet.

    The research chat UI is baked into the image, so a container outliving its
    image keeps serving the old bundles. Recreate-on-session-open alone leaves
    those containers in place until someone happens to open one.
    """
    stale = _Removable("llm4ad-paper-workspace-stale", "sha256:old")
    current = _Removable("llm4ad-paper-workspace-current", "sha256:new")

    removed = _reconcile_with(monkeypatch, [stale, current], image_id="sha256:new")

    assert removed == 1
    assert stale.removed is True
    assert current.removed is False


def test_startup_reconcile_leaves_containers_on_the_current_image_alone(monkeypatch) -> None:
    """A routine restart must not interrupt work that is still valid."""
    running = _Removable("llm4ad-paper-workspace-a", "sha256:new")

    removed = _reconcile_with(monkeypatch, [running], image_id="sha256:new")

    assert removed == 0
    assert running.removed is False


def test_startup_reconcile_skips_when_the_image_is_not_built_yet(monkeypatch) -> None:
    """No image means nothing can be stale against it; report no removals."""
    existing = _Removable("llm4ad-paper-workspace-a", "sha256:old")

    removed = _reconcile_with(monkeypatch, [existing], image_id=None)

    assert removed == 0
    assert existing.removed is False


class _Stoppable:
    """One workspace container the idle sweep may stop."""

    def __init__(self, name: str, status: str = "running") -> None:
        self.name = name
        self.status = status
        self.stop_calls = 0
        self.refuses_to_stop = False

    def stop(self, **_kwargs) -> None:
        self.stop_calls += 1
        if self.refuses_to_stop:
            raise RuntimeError("container refused to stop")
        self.status = "exited"


class _IdleHarness:
    """Wire the idle sweep to fakes for its Redis, DB and Docker edges."""

    def __init__(self, monkeypatch):
        self.retracked: list[str] = []
        self.idle: list[str] = []
        self.busy: list[str] = []
        self.containers: dict[str, _Stoppable] = {}
        self.redis_error = False
        self.db_error = False

        def pop_idle(_threshold):
            if self.redis_error:
                raise RuntimeError("redis unavailable")
            pending, self.idle = self.idle, []
            return pending

        def touch(workspace_id, _ts=None):
            self.retracked.append(str(workspace_id))

        monkeypatch.setattr(paper_workspace_runtime, "pop_idle_paper_workspaces", pop_idle)
        monkeypatch.setattr(paper_workspace_runtime, "touch_paper_workspace_active", touch)

        harness = self

        class _Db:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def exec(self, _statement):
                if harness.db_error:
                    raise RuntimeError("database unavailable")

                class _Result:
                    def all(self):
                        return list(harness.busy)

                return _Result()

        monkeypatch.setattr(paper_workspace_runtime, "get_db_session", _Db)

        class _Containers:
            def get(self, name):
                if name not in harness.containers:
                    raise paper_workspace_runtime.NotFound(name)
                return harness.containers[name]

        monkeypatch.setattr(
            paper_workspace_runtime,
            "get_docker_client",
            lambda: SimpleNamespace(containers=_Containers()),
        )

    def add_running(self, workspace_id, status: str = "running") -> _Stoppable:
        name = paper_workspace_runtime.paper_workspace_container_name(workspace_id)
        container = _Stoppable(name, status)
        self.containers[name] = container
        return container


def test_idle_sweep_stops_a_quiet_workspace_container(monkeypatch) -> None:
    """A workspace nobody is working in gives its memory and CPU back."""
    harness = _IdleHarness(monkeypatch)
    workspace_id = str(uuid.uuid4())
    container = harness.add_running(workspace_id)
    harness.idle = [workspace_id]

    assert paper_workspace_runtime.stop_idle_paper_workspace_containers() == 1
    assert container.status == "exited"


def test_idle_sweep_leaves_a_workspace_with_a_run_in_flight(monkeypatch) -> None:
    """Stopping a mid-stage container kills the agent process inside it.

    The activity timestamp alone cannot carry this: it is written when a stage
    opens and when a result is published, so a stage running longer than the
    idle window would look quiet while it is still working.
    """
    harness = _IdleHarness(monkeypatch)
    workspace_id = str(uuid.uuid4())
    container = harness.add_running(workspace_id)
    harness.idle = [workspace_id]
    harness.busy = [workspace_id]

    assert paper_workspace_runtime.stop_idle_paper_workspace_containers() == 0
    assert container.status == "running"
    # Re-tracked, so it is not re-examined on every pass until it goes quiet
    # again rather than being treated as idle each time.
    assert harness.retracked == [workspace_id]


def test_idle_sweep_leaves_containers_alone_when_run_state_is_unknown(monkeypatch) -> None:
    """A database failure must not be read as "nothing is running"."""
    harness = _IdleHarness(monkeypatch)
    workspace_id = str(uuid.uuid4())
    container = harness.add_running(workspace_id)
    harness.idle = [workspace_id]
    harness.db_error = True

    assert paper_workspace_runtime.stop_idle_paper_workspace_containers() == 0
    assert container.status == "running"
    assert harness.retracked == [workspace_id]


def test_idle_sweep_skips_containers_that_are_already_stopped(monkeypatch) -> None:
    """Re-stopping a stopped container is pointless work."""
    harness = _IdleHarness(monkeypatch)
    workspace_id = str(uuid.uuid4())
    container = harness.add_running(workspace_id, status="exited")
    harness.idle = [workspace_id]

    assert paper_workspace_runtime.stop_idle_paper_workspace_containers() == 0
    assert container.stop_calls == 0


def test_idle_sweep_tolerates_a_deleted_workspace_and_a_failing_stop(monkeypatch) -> None:
    """One bad candidate must not abort the whole sweep."""
    harness = _IdleHarness(monkeypatch)
    stubborn = str(uuid.uuid4())
    healthy = str(uuid.uuid4())
    stubborn_container = harness.add_running(stubborn)
    stubborn_container.refuses_to_stop = True
    healthy_container = harness.add_running(healthy)
    # The middle id has no container: its workspace was deleted.
    harness.idle = [stubborn, str(uuid.uuid4()), healthy]

    assert paper_workspace_runtime.stop_idle_paper_workspace_containers() == 1
    assert stubborn_container.status == "running"
    assert healthy_container.status == "exited"


def test_idle_sweep_returns_zero_when_activity_cannot_be_read(monkeypatch) -> None:
    """Losing Redis degrades to "reclaim nothing", never to a crash."""
    harness = _IdleHarness(monkeypatch)
    workspace_id = str(uuid.uuid4())
    harness.add_running(workspace_id)
    harness.idle = [workspace_id]
    harness.redis_error = True

    assert paper_workspace_runtime.stop_idle_paper_workspace_containers() == 0
