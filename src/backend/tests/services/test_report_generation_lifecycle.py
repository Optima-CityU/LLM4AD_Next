import sys
import uuid
from types import ModuleType, SimpleNamespace

from llm4ad.infra.provider.base import BaseProvider

if "app.services.task_service" not in sys.modules:
    task_service_stub = ModuleType("app.services.task_service")
    task_service_stub.get_task_with_auth = lambda *args, **kwargs: None
    sys.modules["app.services.task_service"] = task_service_stub

from app.core import redis as redis_core
from app.schemas.report import ReportGenerateRequest, ReportStatus, ReportType
from app.services import report_service


def test_report_generation_id_is_cleared_only_by_its_owner(monkeypatch):
    calls: list[tuple] = []

    class FakeRedis:
        def eval(self, *args):
            calls.append(args)
            return 1

    monkeypatch.setattr(redis_core, "get_sync_redis", lambda: FakeRedis())

    cleared = redis_core.clear_report_generation_id(
        "task-1", "champion_birth", "generation-1"
    )

    assert cleared is True
    assert calls[0][1:] == (
        1,
        "report_gen:task-1:champion_birth",
        "generation-1",
    )


def test_stop_report_immediately_persists_cancelled_status(monkeypatch):
    task_id = uuid.uuid4()
    task = SimpleNamespace(
        reports={"champion_birth": {"status": ReportStatus.GENERATING.value}}
    )
    cleared: list[tuple] = []
    persisted: list[tuple] = []

    monkeypatch.setattr(report_service, "get_task_with_auth", lambda *_args: task)
    monkeypatch.setattr(
        report_service,
        "get_report_generation_id",
        lambda *_args: "generation-1",
    )
    monkeypatch.setattr(
        report_service,
        "clear_report_generation_id",
        lambda *args: cleared.append(args) or True,
    )
    monkeypatch.setattr(
        report_service,
        "_persist_report",
        lambda *args, **kwargs: persisted.append((args, kwargs)),
    )
    monkeypatch.setattr(report_service, "push_report_chunk", lambda *_args: None)

    response = report_service.stop_report_generation(
        None, task_id, object(), ReportType.CHAMPION_BIRTH.value
    )

    assert response.status == ReportStatus.CANCELLED
    assert cleared == [
        (str(task_id), ReportType.CHAMPION_BIRTH.value, "generation-1")
    ]
    assert persisted == [
        (
            (
                task_id,
                ReportType.CHAMPION_BIRTH.value,
                ReportStatus.CANCELLED,
                None,
            ),
            {},
        )
    ]


async def test_superseded_generation_does_not_overwrite_latest_report(monkeypatch):
    class FakeProvider:
        async def generate_stream(self, _prompt):
            yield "late chunk"

    monkeypatch.setattr(
        BaseProvider,
        "create",
        staticmethod(lambda *_args, **_kwargs: FakeProvider()),
    )
    monkeypatch.setattr(
        report_service,
        "get_report_generation_id",
        lambda *_args: "new-generation",
    )
    monkeypatch.setattr(report_service, "push_report_chunk", lambda *_args: None)
    monkeypatch.setattr(
        report_service, "clear_report_generation_id", lambda *_args: False
    )
    persisted: list[tuple] = []
    monkeypatch.setattr(
        report_service,
        "_persist_report",
        lambda *args, **kwargs: persisted.append((args, kwargs)),
    )

    await report_service._run_report_generation(
        task_id=uuid.uuid4(),
        report_type=ReportType.CHAMPION_BIRTH.value,
        provider_config={"type": "mock"},
        prompt="report prompt",
        generation_id="old-generation",
        request=ReportGenerateRequest(
            report_type=ReportType.CHAMPION_BIRTH,
            best_node_id="node-1",
        ),
        log_data=[],
        background="",
    )

    assert persisted == []
