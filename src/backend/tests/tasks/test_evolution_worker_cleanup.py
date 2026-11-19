"""Keep worker startup cleanup from interrupting another worker's live job."""

from types import SimpleNamespace
from unittest.mock import Mock

from app.services import container_runtime
from app.tasks import evolution


def test_worker_startup_preserves_live_task_containers(monkeypatch):
    """Remove stopped leftovers while preserving running, paused and new jobs."""
    jobs = [
        SimpleNamespace(name=f"task-{state}", status=state, remove=Mock())
        for state in ("running", "paused", "created", "exited", "dead")
    ]

    def list_containers(*, all, filters):
        assert all
        statuses = filters.get("status")
        return [job for job in jobs if statuses is None or job.status in statuses]

    client = SimpleNamespace(containers=SimpleNamespace(list=list_containers))
    monkeypatch.setattr(container_runtime, "get_docker_client", lambda: client)

    evolution._on_worker_init()

    for job in jobs:
        if job.status in ("exited", "dead"):
            job.remove.assert_called_once_with(force=True, v=True)
        else:
            job.remove.assert_not_called()
