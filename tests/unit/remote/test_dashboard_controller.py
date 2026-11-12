"""Dashboard configuration and service transitions without a browser or network."""

import asyncio
import json
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from llm4ad.gateway.sdk.controller import GatewayController
from llm4ad.gateway.sdk.journal import Journal
from llm4ad.gateway.sdk.runtime import GatewayRuntime, platform_directory
from llm4ad.gateway.settings import DEFAULT_SERVER, GatewaySettings, SettingsStore, load_settings


def configured(tmp_path, **changes):
    """Build complete public settings with a synthetic authorized user."""
    return GatewaySettings(
        adapter="example", allowed_users=[str(uuid.uuid4())], state_dir=str(tmp_path), **changes
    )


class Service:
    """Expose the real runtime binding convention with an entirely idle fake transport."""

    def __init__(self, environment, evaluator):
        """Avoid native dependencies, sockets and production credentials."""
        self.runtime = GatewayRuntime(environment, evaluator)
        self.options = self.runtime.options
        self.bound = self.runtime.bindings()
        self.gateways = {}
        for binding in self.bound:
            binding.directory.mkdir(parents=True, exist_ok=True)
            self.gateways[binding.key] = SimpleNamespace(
                journal=Journal(binding.directory / "jobs.sqlite3"),
                capacity=binding.capacity,
                transport={},
            )
        self.stop = asyncio.Event()
        self.readiness, self.error, self.startup_task = "starting", None, None
        self.admission_paused = False
        self.closed = False

    def set_admission(self, paused):
        """Observe the controller's actual admission decision."""
        self.admission_paused = paused

    async def apply_live_settings(self, **values):
        """Record live settings without replacing accepted work."""
        self.options.update(values)
        for gateway in self.gateways.values():
            gateway.capacity = values["concurrency"]

    async def run(self):
        """Stay ready until the owner performs a controlled service transition."""
        self.readiness = "ready"
        await self.stop.wait()
        self.closed, self.readiness = True, "stopped"

    def health(self):
        """Return only the same public readiness observation a real runtime supplies."""
        return {"ready": self.readiness == "ready"}


async def eventually(predicate):
    """Wait for the controller's normal transition loop."""
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.02)


def test_default_and_configuration_precedence(tmp_path):
    """Web saves override environment defaults while explicit launch options still win."""
    assert GatewaySettings().server == DEFAULT_SERVER
    saved = configured(tmp_path, name="saved")
    SettingsStore(tmp_path).write(saved, None, False)
    values = load_settings(tmp_path, {"LLM4AD_GATEWAY_NAME": "environment"})
    assert values.name == "saved"
    values = load_settings(
        tmp_path, {}, config={"LLM4AD_GATEWAY_NAME": "json"}, explicit={"name": "cli"}
    )
    assert values.name == "cli"
    assert "PASSWORD" not in SettingsStore(tmp_path).path.read_text()


@pytest.mark.parametrize(
    "changes",
    [
        {"concurrency": 0},
        {"cleanup_interval": float("nan")},
        {"allowed_users": ["invalid"]},
        {"server": "file:///tmp/private"},
    ],
)
def test_invalid_settings_never_start_a_service(changes):
    """Use common validation for UI and CLI configuration."""
    with pytest.raises(ValidationError):
        GatewaySettings(**changes)


def test_legacy_identity_is_preserved_and_other_platforms_are_separate(tmp_path):
    """Changing a URL never reuses or destroys another platform's recovery identity."""
    server = "https://fixture.invalid"
    (tmp_path / "identity.json").write_text(
        json.dumps({"server": server + "/api/v1/llm4ad/evaluation-gateways"})
    )
    assert platform_directory(tmp_path, server) == tmp_path
    assert platform_directory(tmp_path, "https://other.invalid") != tmp_path
    assert platform_directory(tmp_path, "https://other.invalid") == platform_directory(
        tmp_path, "https://other.invalid"
    )


def test_cli_keeps_legacy_and_namespaced_device_journals_discoverable(tmp_path):
    """Offline status and recovery keep locating journals after dashboard adoption."""
    from llm4ad.gateway.cli import _journal_paths

    paths = [
        tmp_path / suffix
        for suffix in (
            "jobs.sqlite3",
            "devices/phone/jobs.sqlite3",
            "platforms/site/jobs.sqlite3",
            "platforms/site/devices/phone/jobs.sqlite3",
        )
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        Journal(path)
    assert set(_journal_paths(tmp_path)) == set(paths)


async def test_first_setup_starts_only_once(tmp_path):
    """Opening/reloading pages cannot create another SDK runtime."""
    calls = []

    def factory(environment, evaluator):
        calls.append(evaluator)
        return Service(environment, evaluator)

    controller = GatewayController(
        GatewaySettings(state_dir=str(tmp_path)), tmp_path, environment={}, service_factory=factory
    )
    await controller.start()
    await controller.start()
    try:
        assert controller.status == "configuration_required" and calls == []
        await controller.apply_settings(
            {"adapter": "example", "allowed_users": [str(uuid.uuid4())]}
        )
        await eventually(lambda: controller.status == "ready")
        assert len(calls) == 1
    finally:
        await controller.close()


async def test_second_controller_cannot_take_over_the_same_control_directory(tmp_path):
    """A failed duplicate launch releases only its own successfully acquired resources."""
    first = GatewayController(
        configured(tmp_path), tmp_path, environment={}, service_factory=Service
    )
    second = GatewayController(first.settings, tmp_path, environment={}, service_factory=Service)
    await first.start()
    try:
        with pytest.raises(sqlite3.OperationalError):
            await second.start()
        await second.close()
        assert second.service is None and second._lease is None
        await eventually(lambda: first.status == "ready")
        assert first.service and not first.service.closed
    finally:
        await first.close()


async def test_live_capacity_and_admission_do_not_restart_the_service(tmp_path):
    """Parameter edits affect new admission, never the existing worker lifecycle."""
    controller = GatewayController(
        configured(tmp_path), tmp_path, environment={}, service_factory=Service
    )
    await controller.start()
    try:
        await eventually(lambda: controller.status == "ready")
        service = controller.service
        await controller.apply_settings({"concurrency": 3})
        assert controller.service is service and service.gateways["gateway"].capacity == 3
        await controller.apply_settings({"local_log_keep_seconds": 3600})
        assert controller.service is service and service.options["local_log_keep_seconds"] == 3600
        assert controller.settings.local_log_keep_seconds == 3600
        await controller.pause()
        assert service.admission_paused
        await controller.resume()
        assert not service.admission_paused
        assert controller.store.read()["applied"]["concurrency"] == 3
    finally:
        await controller.close()


async def test_connection_change_waits_for_work_and_upload_ack(tmp_path):
    """A completed computation still blocks switching until its result is acknowledged."""
    controller = GatewayController(
        configured(tmp_path), tmp_path, environment={}, service_factory=Service
    )
    await controller.start()
    try:
        await eventually(lambda: controller.status == "ready")
        previous = controller.service
        journal = previous.gateways["gateway"].journal
        job = {"id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4())}
        journal.accept(job)
        await controller.apply_settings({"server": "https://replacement.invalid"})
        assert controller.settings.server == DEFAULT_SERVER
        assert previous.admission_paused and controller.pending_settings is not None
        journal.update(job["id"], "completed", {"score": 1})
        await asyncio.sleep(0.6)
        assert controller.service is previous
        journal.acknowledge(job["id"], journal.get(job["id"])["seq"])
        await eventually(
            lambda: controller.service is not previous and controller.status == "ready"
        )
        assert previous.closed and controller.settings.server == "https://replacement.invalid"
        assert journal.get(job["id"])["state"] == "completed"
    finally:
        await controller.close()


async def test_restart_keeps_applied_config_until_pending_work_finishes(tmp_path):
    """A crash during drain cannot redirect old work to the new platform."""
    settings = configured(tmp_path)
    pending = GatewaySettings.model_validate(
        {**settings.model_dump(), "server": "https://pending.invalid"}
    )
    directory = platform_directory(tmp_path, settings.server)
    directory.mkdir(parents=True)
    journal = Journal(directory / "jobs.sqlite3")
    journal.accept({"id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4())})
    SettingsStore(tmp_path).write(settings, pending, False)
    restored = load_settings(tmp_path, {})
    controller = GatewayController(restored, tmp_path, environment={}, service_factory=Service)
    await controller.start()
    try:
        await eventually(lambda: controller.service is not None)
        assert controller.service.options["server"] == DEFAULT_SERVER
        assert controller.service.admission_paused
        assert controller.pending_settings.server == "https://pending.invalid"
        await controller.discard_pending()
        assert not controller.service.admission_paused
    finally:
        await controller.close()


@pytest.mark.parametrize("task_journal_exists", [False, True])
async def test_restart_drains_unresolved_startup_without_waiting_for_its_upload_ack(
    tmp_path, task_journal_exists
):
    """Deferred settings retain the startup owner until local recovery is resolved."""
    from llm4ad.gateway.sdk.journal import Reporter

    settings = configured(tmp_path)
    pending = settings.model_copy(update={"server": "https://pending.invalid"})
    directory = platform_directory(tmp_path, settings.server)
    directory.mkdir(parents=True)
    if task_journal_exists:
        Journal(directory / "jobs.sqlite3")
    startup = Journal(directory / "startup.sqlite3")
    job = {"id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4())}
    startup.accept(job)
    Reporter(startup, job["id"]).save_state({"external_job_id": "unresolved-startup"})
    startup.update(job["id"], "recovery_required", {"message": "Restoration unconfirmed"})
    SettingsStore(tmp_path).write(settings, pending, False)
    controller = GatewayController(
        load_settings(tmp_path, {}), tmp_path, environment={}, service_factory=Service
    )
    assert not await controller._idle()
    await controller.start()
    try:
        await eventually(lambda: controller.service is not None)
        previous = controller.service
        assert controller.settings.server == settings.server
        assert previous.admission_paused
        assert controller.pending_settings.server == pending.server
        startup.update(job["id"], "cancelled", {"message": "Restoration confirmed"})
        row = startup.get(job["id"])
        assert row["ack"] < row["seq"]
        await eventually(
            lambda: controller.settings.server == pending.server and controller.status == "ready"
        )
        assert previous.closed
        assert startup.get(job["id"]) == row
    finally:
        await controller.close()


async def test_failed_replacement_restores_previous_service(tmp_path):
    """A bad replacement leaves the control panel and previous configuration usable."""

    def factory(environment, evaluator):
        if environment["LLM4AD_SERVER_URL"] == "https://bad.invalid":
            raise ValueError("Synthetic invalid replacement")
        return Service(environment, evaluator)

    controller = GatewayController(
        configured(tmp_path), tmp_path, environment={}, service_factory=factory
    )
    await controller.start()
    try:
        await eventually(lambda: controller.status == "ready")
        await controller.apply_settings({"server": "https://bad.invalid"})
        await eventually(
            lambda: bool(controller.pending_error) and controller.status == "draining_for_settings"
        )
        assert controller.settings.server == DEFAULT_SERVER
        assert controller.pending_settings.server == "https://bad.invalid"
        assert controller.store.read()["pending_error"] == "Synthetic invalid replacement"
    finally:
        await controller.close()


async def test_startup_override_changes_pending_target_without_abandoning_old_work(tmp_path):
    """Explicit startup options remain highest priority after safe recovery/drain."""
    saved = configured(tmp_path)
    pending = GatewaySettings.model_validate(
        {**saved.model_dump(), "server": "https://pending.invalid"}
    )
    SettingsStore(tmp_path).write(saved, pending, False)
    directory = platform_directory(tmp_path, saved.server)
    directory.mkdir(parents=True)
    Journal(directory / "jobs.sqlite3").accept(
        {"id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4())}
    )
    explicit = load_settings(
        tmp_path, {}, explicit={"server": "https://override.invalid", "concurrency": 2}
    )
    controller = GatewayController(explicit, tmp_path, environment={}, service_factory=Service)
    await controller.start()
    try:
        await eventually(lambda: controller.service is not None)
        assert controller.settings.server == DEFAULT_SERVER
        assert controller.settings.concurrency == 2
        assert controller.pending_settings.server == "https://override.invalid"
    finally:
        await controller.close()


async def test_hot_capacity_is_applied_while_connection_change_is_pending(tmp_path):
    """Pending cold edits do not unnecessarily defer a later capacity adjustment."""
    controller = GatewayController(
        configured(tmp_path), tmp_path, environment={}, service_factory=Service
    )
    await controller.start()
    try:
        await eventually(lambda: controller.status == "ready")
        journal = controller.service.gateways["gateway"].journal
        journal.accept({"id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4())})
        await controller.apply_settings({"server": "https://pending.invalid"})
        await controller.apply_settings({"concurrency": 4})
        assert controller.service.gateways["gateway"].capacity == 4
        assert controller.settings.server == DEFAULT_SERVER
        assert controller.pending_settings.concurrency == 4
    finally:
        await controller.close()


def monitoring_job(**changes):
    """Describe a local record usable by the shared task and startup readers."""
    return {
        "id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4()), "task_id": str(uuid.uuid4()),
        "algorithm_name": "fixture-candidate", "timeout": 60, "project_root": "/fixture/candidate", **changes,
    }


async def test_merged_monitoring_keeps_friendly_names_and_legacy_order(tmp_path):
    """Merging device pages follows the same created/start/ID ordering as each journal."""
    settings = configured(tmp_path)
    controller = GatewayController(settings, tmp_path, environment={})
    directory = platform_directory(tmp_path, settings.server)
    sources = [directory / "jobs.sqlite3", directory / "devices/opaque-device/jobs.sqlite3"]
    for path in sources:
        path.parent.mkdir(parents=True, exist_ok=True)
    recent, legacy, oldest = monitoring_job(id="b-recent"), monitoring_job(id="a-legacy"), monitoring_job(id="c-oldest")
    for path, job, created, started in [
        (sources[0], recent, 200, None), (sources[1], legacy, None, 300), (sources[0], oldest, 100, None),
    ]:
        journal = Journal(path)
        journal.accept(job)
        with journal.connect() as db:
            db.execute("UPDATE jobs SET created_at=?,started=? WHERE id=?", (created, started, job["id"]))
    controller.service = SimpleNamespace(bound=[
        SimpleNamespace(key="gateway", name="Friendly workstation", directory=sources[0].parent),
        SimpleNamespace(key="opaque-device", name="Friendly laboratory phone", directory=sources[1].parent),
    ])
    first = await controller.list_jobs(page_size=1)
    second = await controller.list_jobs(page=2, page_size=1)
    assert first["total"] == second["total"] == 3
    assert first["items"][0]["job_id"] == legacy["id"]
    assert first["items"][0]["gateway_name"] == "Friendly laboratory phone"
    assert second["items"][0]["job_id"] == recent["id"]
    assert second["items"][0]["gateway_name"] == "Friendly workstation"
    detail = await controller.get_job("opaque-device", legacy["id"], legacy["attempt_id"])
    assert detail["binding"] == "opaque-device" and detail["gateway_name"] == "Friendly laboratory phone"
    with pytest.raises(ValueError):
        await controller.list_jobs(page=0)


async def test_startup_reads_only_known_local_journal_without_starting_checks(tmp_path):
    """Startup details, messages and snapshot summaries never become platform jobs."""
    from unittest.mock import Mock

    from llm4ad.gateway.sdk.journal import Reporter

    settings = configured(tmp_path)
    controller = GatewayController(settings, tmp_path, environment={})
    controller.service = Service(settings.environment({}), settings.evaluator_spec)
    controller.service.runtime.check = Mock(side_effect=AssertionError("Reading a startup record triggered a check"))
    directory = controller.service.bound[0].directory
    assert await controller.get_startup("gateway") is None
    assert not (directory / "startup.sqlite3").exists()
    startup = Journal(directory / "startup.sqlite3")
    older, latest = monitoring_job(), monitoring_job()
    for job, created in [(older, 10), (latest, 20)]:
        startup.accept(job)
        reporter = Reporter(startup, job["id"])
        reporter.save_state({"external_id": "PRIVATE-SAVED"})
        reporter.message("Local startup progress", data={"path": "/private/startup", "samples": 3})
        with startup.connect() as db:
            db.execute("UPDATE jobs SET created_at=? WHERE id=?", (created, job["id"]))
    before = startup.rows()
    detail = await controller.get_startup("gateway")
    assert detail["job_id"] == latest["id"] and detail["attempt_id"] == latest["attempt_id"]
    assert detail["purpose"] == detail["job"]["purpose"] == "startup"
    assert detail["pending_upload"] is detail["can_cancel"] is False
    assert "PRIVATE-SAVED" not in json.dumps(detail) and "/private/" not in json.dumps(detail)
    same = await controller.get_job("gateway", latest["id"], latest["attempt_id"], purpose="startup")
    assert same["job_id"] == latest["id"]
    messages = await controller.get_messages("gateway", latest["id"], latest["attempt_id"], purpose="startup")
    assert messages["items"][0]["data"] == {"samples": 3}
    assert (await controller.list_jobs())["total"] == 0
    snapshot = await controller.get_snapshot()
    assert snapshot["bindings"][0]["startup"]["job_id"] == latest["id"]
    assert snapshot["summary"]["total"] == 0
    assert startup.rows() == before
    controller.service.runtime.check.assert_not_called()
    with pytest.raises(ValueError, match="purpose"):
        await controller.get_job("gateway", latest["id"], latest["attempt_id"], purpose="../../other.sqlite3")
    with pytest.raises(ValueError):
        await controller.get_startup("unknown-binding")
    with pytest.raises(LookupError):
        await controller.get_job("gateway", latest["id"], latest["attempt_id"])
    replacement = {**latest, "attempt_id": str(uuid.uuid4())}
    startup.accept(replacement)
    with pytest.raises(ValueError, match="current"):
        await controller.get_messages("gateway", latest["id"], latest["attempt_id"], purpose="startup")


@pytest.mark.parametrize("purpose", ["task", "startup"])
async def test_controller_local_log_facade_discovers_safe_sources_off_thread(tmp_path, purpose):
    """Local source browsing remains fenced and returns only public store projections."""
    import threading
    from unittest.mock import Mock

    from llm4ad.gateway.sdk.contracts import RemoteJob
    from llm4ad.gateway.sdk.journal import Reporter

    store_type = pytest.importorskip("llm4ad.gateway.sdk.local_logs").LocalLogStore
    settings = configured(tmp_path)
    controller = GatewayController(settings, tmp_path, environment={})
    controller.service = Service(settings.environment({}), settings.evaluator_spec)
    directory = controller.service.bound[0].directory
    journal = Journal(directory / ("jobs.sqlite3" if purpose == "task" else "startup.sqlite3"))
    job = monitoring_job()
    journal.accept(job)
    Reporter(journal, job["id"]).save_state({"external_id": "PRIVATE-HANDLE"})
    store = store_type(journal)
    stdout = store.create(job["id"], job["attempt_id"], "worker.stdout", description="Worker stdout")
    stdout.write_text("first\nsecond\n")
    backend = tmp_path / "backend.stdout"
    backend.write_text("backend output\n")
    threads = []

    def discover(actual, saved):
        assert isinstance(actual, RemoteJob) and actual.id == job["id"]
        assert saved == {"external_id": "PRIVATE-HANDLE"}
        threads.append(threading.get_ident())
        return {"executor.stdout": {"path": backend, "description": "External executor stdout"}}

    controller.service.runtime.local_logs = Mock(side_effect=discover)
    sources = await controller.list_local_logs("gateway", job["id"], job["attempt_id"], purpose=purpose)
    assert {item["name"] for item in sources} == {"worker.stdout", "executor.stdout"}
    assert all({"name", "description", "available", "size", "updated_at"} <= item.keys() for item in sources)
    assert all(set(item) <= {"name", "description", "available", "size", "updated_at", "expired"} for item in sources)
    assert all(item["available"] for item in sources)
    assert str(tmp_path) not in json.dumps(sources) and "PRIVATE-HANDLE" not in json.dumps(sources)
    data = await controller.read_local_log("gateway", job["id"], job["attempt_id"], "worker.stdout", offset=0, limit=6, purpose=purpose)
    assert data["text"] == "first\n" and data["offset"] == 0 and data["next_offset"] == 6
    assert set(data) == {"text", "offset", "next_offset", "size", "eof", "reset", "available"}
    snapshot = await controller.local_log_snapshot("gateway", job["id"], job["attempt_id"], "executor.stdout", purpose=purpose)
    try:
        assert isinstance(snapshot, Path) and snapshot != backend
        assert snapshot.read_bytes() == backend.read_bytes()
    finally:
        snapshot.unlink(missing_ok=True)
    assert threads and all(identifier != threading.get_ident() for identifier in threads)
    replacement = {**job, "attempt_id": str(uuid.uuid4())}
    journal.accept(replacement)
    with pytest.raises(ValueError, match="current"):
        await controller.read_local_log("gateway", job["id"], job["attempt_id"], "worker.stdout", purpose=purpose)


@pytest.fixture
def local_log_controller(tmp_path):
    """Prepare the real private log catalog behind a transport-free controller."""
    from unittest.mock import Mock

    from llm4ad.gateway.sdk.local_logs import LocalLogStore

    settings = configured(tmp_path)
    controller = GatewayController(settings, tmp_path, environment={})
    controller.service = Service(settings.environment({}), settings.evaluator_spec)
    journal = controller.service.gateways["gateway"].journal
    job = monitoring_job()
    journal.accept(job)
    controller.service.runtime.local_logs = Mock(return_value={})
    return controller, journal, job, LocalLogStore(journal)


@pytest.mark.parametrize("purpose", ["task", "startup"])
async def test_discovery_failure_retains_only_valid_managed_sources(local_log_controller, tmp_path, purpose):
    """Lost backend receipts cannot hide private logs or authorize old external paths."""
    from llm4ad.gateway.sdk.journal import Reporter

    controller, journal, job, store = local_log_controller
    if purpose == "startup":
        journal = Journal(Path(journal.path).with_name("startup.sqlite3"))
        journal.accept(job)
        store = type(store)(journal)
    worker = store.create(job["id"], job["attempt_id"], "worker.stdout")
    worker.write_text("retained worker output\n")
    reported = Reporter(journal, job["id"]).local_log("solver/local.log", description="Solver output")
    reported.write_text("retained solver output\n")
    external = tmp_path / "external.stdout"
    external.write_text("external output must not be exposed\n")
    store.register_external(job["id"], job["attempt_id"], "backend.stdout", external)
    before = journal.get(job["id"])
    failure = ValueError("Backend ownership receipt is unavailable")
    controller.service.runtime.local_logs.side_effect = failure
    sources = await controller.list_local_logs("gateway", job["id"], job["attempt_id"], purpose=purpose)
    assert {item["name"] for item in sources} == {"worker.stdout", "solver/local.log"}
    assert all(item["available"] for item in sources)
    result = await controller.read_local_log("gateway", job["id"], job["attempt_id"], "worker.stdout", purpose=purpose)
    assert result["text"] == worker.read_text()
    snapshot = await controller.local_log_snapshot("gateway", job["id"], job["attempt_id"], "solver/local.log", purpose=purpose)
    try:
        assert snapshot.read_bytes() == reported.read_bytes()
    finally:
        snapshot.unlink(missing_ok=True)
    for operation in (controller.read_local_log, controller.local_log_snapshot):
        with pytest.raises(ValueError) as caught:
            await operation("gateway", job["id"], job["attempt_id"], "backend.stdout", purpose=purpose)
        assert caught.value is failure
    assert journal.get(job["id"]) == before


@pytest.mark.parametrize("catalog", ["empty", "external-only", "missing", "forged", "expired"])
async def test_discovery_failure_without_safe_private_copy_propagates(local_log_controller, tmp_path, catalog):
    """Unavailable, expired or incorrectly placed files cannot enable a fallback."""
    controller, _, job, store = local_log_controller
    if catalog != "empty":
        if catalog == "external-only":
            path = tmp_path / "external.log"
            path.write_text("external only")
            store.register_external(job["id"], job["attempt_id"], "backend.stdout", path)
        else:
            path = store.create(job["id"], job["attempt_id"], "worker.stdout")
            path.write_text("private copy")
            if catalog == "missing":
                path.unlink()
            elif catalog in {"forged", "expired"}:
                manifest = store.directory(job["id"], job["attempt_id"]) / "manifest.json"
                data = json.loads(manifest.read_text())
                if catalog == "forged":
                    other = tmp_path / "outside.log"
                    other.write_text("outside private storage")
                    data["sources"]["worker.stdout"]["path"] = str(other)
                else:
                    data["expired_at"] = 1
                manifest.write_text(json.dumps(data))
    failure = RuntimeError("Discovery unavailable")
    controller.service.runtime.local_logs.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        await controller.list_local_logs("gateway", job["id"], job["attempt_id"])
    assert caught.value is failure


async def test_fallback_never_bypasses_attempt_fences(local_log_controller):
    """Check both the initial fence and replacement while discovery is outstanding."""
    controller, journal, job, store = local_log_controller
    store.create(job["id"], job["attempt_id"], "worker.stdout").write_text("old private output")
    discover = controller.service.runtime.local_logs
    with pytest.raises(ValueError, match="current"):
        await controller.list_local_logs("gateway", job["id"], str(uuid.uuid4()))
    discover.assert_not_called()

    def replace(*args):
        journal.accept({**job, "attempt_id": str(uuid.uuid4())})
        raise ValueError("Backend discovery failed after replacement")

    discover.side_effect = replace
    with pytest.raises(ValueError, match="current"):
        await controller.read_local_log("gateway", job["id"], job["attempt_id"], "worker.stdout")


@pytest.mark.parametrize("invalid", ["name", "path", "description"])
async def test_discovered_mapping_is_validated_before_any_registration(local_log_controller, tmp_path, monkeypatch, invalid):
    """A later bad entry cannot partly register earlier external sources."""
    from unittest.mock import Mock

    controller, journal, job, store = local_log_controller
    store.create(job["id"], job["attempt_id"], "worker.stdout").write_text("safe private copy")
    external = tmp_path / "valid-external.log"
    external.write_text("not registered on validation failure")
    mapping = {"first.stdout": external}
    if invalid == "name":
        mapping["../invalid.stdout"] = external
    elif invalid == "path":
        mapping["invalid.stdout"] = tmp_path
    else:
        mapping["invalid.stdout"] = {"path": external, "description": {"not": "text"}}
    controller.service.runtime.local_logs.return_value = mapping
    register = Mock(side_effect=AssertionError("Partially validated mapping was registered"))
    monkeypatch.setattr(type(store), "register_external", register)
    before = store._load(job["id"], job["attempt_id"])
    sources = await controller.list_local_logs("gateway", job["id"], job["attempt_id"])
    assert [item["name"] for item in sources] == ["worker.stdout"]
    assert store._load(job["id"], job["attempt_id"]) == before
    register.assert_not_called()
    assert journal.get(job["id"])["state"] == "accepted"
