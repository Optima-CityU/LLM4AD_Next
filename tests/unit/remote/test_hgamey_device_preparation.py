"""Verify device preflight and restoration decisions without contacting a device."""

import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from llm4ad.gateway.examples.hgamey.execution import device, executor, process

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(os.name == "nt", reason="Device preparation requires POSIX"),
]


@pytest.fixture
def preparation_job(tmp_path, monkeypatch):
    """Create a frozen job with an offline synthetic transport and guarded mutations."""
    folder = tmp_path / "jobs" / ("a" * 32)
    folder.mkdir(parents=True)
    frozen_device = {
        "id": "fixture-phone",
        "endpoint": "192.0.2.10:5555",
        "expected_serial": "fixture-serial",
    }
    frozen = {
        "device": frozen_device,
        "submission": {
            "operation": "eval",
            "request": {"version": 2, "kind": "shader", "workload": {"carrier": "headless"}},
        },
    }
    device.write_json(folder / "input.json", frozen)
    marker = {
        "schema": "hgamey/device-preparation@1",
        "job_id": folder.name,
        "device_id": frozen_device["id"],
    }
    job = SimpleNamespace(
        folder=folder,
        frozen=frozen,
        marker=marker,
        events=[],
        mutations=[],
        online=False,
        failure=None,
        foreground="mCurrentFocus=Window{123 u0 org.example.previous/.MainActivity}",
    )
    persist = device.write_json

    def record(action, *, mutating=False):
        path = folder / "device-preparation.json"
        snapshot = device.read_json(path) if path.exists() else None
        job.events.append((action, snapshot))
        if mutating:
            job.mutations.append(action)
            assert snapshot == {**marker, "device_started": True}, (
                "Mutation preceded the persisted started marker"
            )

    def write_json(path, value):
        persist(path, value)
        if (
            path.name == "device-preparation.json"
            and isinstance(value, dict)
            and type(value.get("device_started")) is bool
        ):
            record("started" if value["device_started"] else "prepared")

    def adb(*args, timeout=30):
        if args == ("connect", frozen_device["endpoint"]):
            action, output = "connect", "already connected to " + frozen_device["endpoint"]
            assert timeout == 20
        else:
            assert args[:2] == ("-s", frozen_device["endpoint"])
            tail = args[2:]
            if tail == ("wait-for-device",):
                assert 0 < timeout <= 30
                action, output = "wait", ""
            elif tail == ("get-state",):
                action = "get-state"
                output = "device" if job.online and job.failure != "offline" else "offline"
            elif tail == ("shell", "getprop", "ro.serialno"):
                action = "identity"
                output = (
                    "another-physical-phone"
                    if job.failure == "identity-mismatch"
                    else frozen_device["expected_serial"]
                )
            elif tail == ("shell", "cat", "/proc/sys/kernel/random/boot_id"):
                action, output = "boot", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            elif tail == ("shell", "dumpsys window"):
                action, output = "window", job.foreground
            elif tail == ("shell", "dumpsys activity activities"):
                action, output = "activity", ""
            elif tail == ("shell", "am", "force-stop", "org.example.previous"):
                action, output = "pause", ""
            elif tail == (
                "shell",
                "getprop ro.product.model; getprop ro.build.version.release; su -c id",
            ):
                action, output = "probe", "Fixture model / uid=0(root)"
            else:
                pytest.fail(f"Unexpected synthetic ADB command: {args!r}")
        record(action, mutating=action == "pause")
        if action == job.failure:
            if action == "wait":
                raise subprocess.TimeoutExpired(args, timeout)
            raise RuntimeError("synthetic preflight failure: " + action)
        if action == "wait":
            job.online = True
        return output

    def command(argv, **options):
        if options.get("pf"):
            record("backend", mutating=True)
            operation = job.frozen["submission"]["operation"]
            if operation == "game-ab":
                assert argv[1:3] == ["-m", "llm4ad.gateway.examples.hgamey.protocol.game_ab"]
            else:
                assert argv[:2] == ["phonefarm", "eval" if operation == "eval" else "gray-layer"]
            report = {"status": "PASS", "conditions_observed": {"restored": True}}
            options["stdout"].write(json.dumps(report))
            if operation == "game-ab":
                persist(folder / "result.json", report)
        else:
            assert argv[1:] == [
                "-s",
                frozen_device["endpoint"],
                "shell",
                "am",
                "start",
                "-W",
                "-n",
                "org.example.previous/.MainActivity",
            ]
            record("restore", mutating=True)
        return SimpleNamespace(returncode=0, stdout="Status: ok\n", stderr="")

    monkeypatch.setattr(
        process.subprocess,
        "run",
        Mock(side_effect=AssertionError("Real device commands must never run")),
    )
    monkeypatch.setattr(device, "inherited_fds", lambda: (123,))
    monkeypatch.setattr(device, "write_json", write_json)
    monkeypatch.setattr(device, "adb", adb)
    monkeypatch.setattr(device, "command", command)
    return job


def configure_submission(job, operation="eval", pause=False):
    """Persist the requested synthetic operation while keeping the device frozen."""
    submission = {"operation": operation, "pause_foreground": pause}
    if operation == "eval":
        submission["request"] = {
            "version": 2,
            "kind": "shader",
            "workload": {"carrier": "headless"},
        }
    elif operation == "game-ab":
        submission["options"] = {"package": "org.example.target"}
    elif operation == "gray-loadop":
        submission["options"] = {"frames": 600, "intensity": 2, "timeout_seconds": 60}
    job.frozen["submission"] = submission
    device.write_json(job.folder / "input.json", job.frozen)


@pytest.mark.parametrize("operation", ["eval", "game-ab", "gray-loadop"])
@pytest.mark.parametrize("pause", [False, True])
def test_transient_offline_transport_waits_before_identity_and_first_mutation(
    preparation_job, operation, pause
):
    """An already-connected offline endpoint becomes ready before any workload changes."""
    job = preparation_job
    configure_submission(job, operation, pause)

    assert device.execute_on_device(job.folder) == 0

    actions = [action for action, _ in job.events]
    assert (
        actions.index("prepared")
        < actions.index("connect")
        < actions.index("wait")
        < actions.index("get-state")
    )
    assert (
        actions.index("get-state")
        < actions.index("identity")
        < actions.index("window")
        < actions.index("started")
    )
    assert actions.count("wait") == 1
    expected_mutations = ["pause", "backend", "restore"] if pause else ["backend"]
    if not pause and operation != "eval":
        expected_mutations.append("restore")
    assert job.mutations == expected_mutations
    assert actions.index("started") < actions.index(job.mutations[0])
    for action, snapshot in job.events:
        assert snapshot == {
            **job.marker,
            "device_started": action
            not in {"prepared", "connect", "wait", "get-state", "identity", "boot", "window"},
        }
    assert device.cleanup_completed(job.folder, operation) is True
    process.subprocess.run.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        "connect",
        "wait",
        "get-state",
        "offline",
        "identity",
        "identity-mismatch",
        "window",
        "activity",
        "probe",
    ],
)
def test_preflight_failure_confirms_noop_cleanup_without_mutation(
    preparation_job, monkeypatch, failure
):
    """Transport, identity and read-only failures keep an explicit untouched-device receipt."""
    job = preparation_job
    job.failure = failure
    operation = "probe" if failure == "probe" else "eval"
    configure_submission(job, operation, pause=True)
    if failure == "activity":
        job.foreground = ""
    monkeypatch.setattr(executor.sys, "argv", ["executor", "_device", str(job.folder)])

    assert executor.main() == 2

    preparation = {**job.marker, "device_started": False}
    assert device.read_json(job.folder / "device-preparation.json") == preparation
    report = device.read_json(job.folder / "result.json")
    assert report["status"] == "ERROR"
    assert report["preparation"] == preparation
    assert report["external_cleanup_confirmed"] is True
    failed_action = {"offline": "get-state", "identity-mismatch": "identity"}.get(failure, failure)
    assert job.events[-1][0] == failed_action
    if failure == "offline":
        assert "device not ready" in report["error"]
    elif failure == "identity-mismatch":
        assert "identity mismatch" in report["error"]
    elif failure == "wait":
        assert "timed out" in report["error"]
    else:
        assert report["error"] == "synthetic preflight failure: " + failure
    assert device.cleanup_completed(job.folder, operation) is True
    assert job.mutations == []
    assert not {
        "backend-command.json",
        "backend.stdout",
        "foreground.json",
        "worker-error.json",
    } & {p.name for p in job.folder.iterdir()}
    process.subprocess.run.assert_not_called()


def test_successful_probe_keeps_not_started_marker(preparation_job):
    """Reading probe facts never claims that a mutating workload began."""
    job = preparation_job
    configure_submission(job, "probe")

    assert device.execute_on_device(job.folder) == 0

    assert [action for action, _ in job.events] == [
        "prepared",
        "connect",
        "wait",
        "get-state",
        "identity",
        "boot",
        "probe",
    ]
    assert all(snapshot == {**job.marker, "device_started": False} for _, snapshot in job.events)
    assert device.read_json(job.folder / "result.json")["root_verified"] is True
    assert job.mutations == []
    assert device.cleanup_completed(job.folder, "probe") is True


@pytest.mark.parametrize("operation,pause", [
    ("game-ab", False), ("game-ab", True), ("gray-loadop", False), ("eval", True),
])
def test_unknown_foreground_stops_before_creating_restoration_obligation(preparation_job, operation, pause):
    """Missing foreground identity must remain a retryable read-only preparation failure."""
    job = preparation_job
    job.foreground = ""
    configure_submission(job, operation, pause)
    with pytest.raises(RuntimeError, match="foreground"):
        device.execute_on_device(job.folder)
    assert job.mutations == []
    assert device.read_json(job.folder / "device-preparation.json")["device_started"] is False
    assert not (job.folder / "foreground.json").exists()
    assert device.cleanup_completed(job.folder, operation) is True


def test_headless_without_foreground_pause_needs_no_activity_snapshot(preparation_job):
    """A pure headless benchmark can finish without changing the visible activity."""
    job = preparation_job
    job.foreground = ""
    configure_submission(job, "eval", pause=False)
    assert device.execute_on_device(job.folder) == 0
    assert job.mutations == ["backend"]
    assert device.cleanup_completed(job.folder, "eval") is True


@pytest.mark.parametrize("started", [False, True])
@pytest.mark.parametrize("pause", [False, True])
def test_failed_preparation_persistence_prevents_mutation(
    preparation_job, monkeypatch, started, pause
):
    """A failed durable marker write stops backend and foreground actions."""
    job = preparation_job
    configure_submission(job, pause=pause)
    persist = device.write_json

    def fail_marker(path, value):
        if path.name == "device-preparation.json" and value["device_started"] is started:
            raise OSError("synthetic preparation persistence failure")
        persist(path, value)

    monkeypatch.setattr(device, "write_json", fail_marker)
    with pytest.raises(OSError, match="synthetic preparation persistence failure"):
        device.execute_on_device(job.folder)

    assert job.mutations == []
    assert not (job.folder / "backend-command.json").exists()
    assert not (job.folder / "backend.stdout").exists()
    if not started:
        assert job.events == []
    else:
        assert device.read_json(job.folder / "device-preparation.json") == {
            **job.marker,
            "device_started": False,
        }
    process.subprocess.run.assert_not_called()


@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "started",
        "foreign-job",
        "foreign-device",
        "wrong-schema",
        "missing-started",
        "zero",
        "string-false",
        "invalid-json",
        "list",
    ],
)
def test_cleanup_needs_explicit_same_job_not_started_marker(preparation_job, invalid):
    """Absent, malformed or unrelated preparation evidence cannot certify no-op cleanup."""
    job = preparation_job
    marker = {**job.marker, "device_started": False}
    if invalid == "started":
        marker["device_started"] = True
    elif invalid == "foreign-job":
        marker["job_id"] = "b" * 32
    elif invalid == "foreign-device":
        marker["device_id"] = "another-phone"
    elif invalid == "wrong-schema":
        marker["schema"] = "hgamey/device-preparation@0"
    elif invalid == "missing-started":
        marker.pop("device_started")
    elif invalid == "zero":
        marker["device_started"] = 0
    elif invalid == "string-false":
        marker["device_started"] = "false"
    path = job.folder / "device-preparation.json"
    if invalid == "invalid-json":
        path.write_text('{"device_started":')
    elif invalid == "list":
        device.write_json(path, [])
    elif invalid != "missing":
        device.write_json(path, marker)
    device.write_json(
        job.folder / "result.json", {"status": "ERROR", "error": "synthetic interrupted job"}
    )

    assert device.cleanup_completed(job.folder, "eval") is False


@pytest.mark.parametrize(
    "evidence", ["backend-command.json", "backend.stdout", "foreground.json", "worker-error.json"]
)
def test_execution_evidence_disqualifies_not_started_cleanup(preparation_job, evidence):
    """Every sign of execution overrides an otherwise valid not-started marker."""
    job = preparation_job
    device.write_json(
        job.folder / "device-preparation.json", {**job.marker, "device_started": False}
    )
    device.write_json(job.folder / "result.json", {"status": "ERROR"})
    assert device.cleanup_completed(job.folder, "eval") is True

    (job.folder / evidence).write_text("{}")

    assert device.cleanup_completed(job.folder, "eval") is False


@pytest.mark.parametrize("marker_state", ["missing", "started", "foreign-job"])
def test_untrusted_marker_preserves_backend_and_foreground_restoration_checks(
    preparation_job, marker_state
):
    """Started and legacy jobs still require both backend and foreground restoration."""
    job = preparation_job
    if marker_state != "missing":
        marker = {**job.marker, "device_started": marker_state == "started"}
        if marker_state == "foreign-job":
            marker["job_id"] = "b" * 32
        device.write_json(job.folder / "device-preparation.json", marker)
    report = {"conditions_observed": {"restored": True}}
    foreground = {"restore_required": True, "restored": True, "restore_exit_code": 0}
    device.write_json(job.folder / "result.json", report)
    device.write_json(job.folder / "foreground.json", foreground)
    assert device.cleanup_completed(job.folder, "eval") is True

    device.write_json(job.folder / "result.json", {"conditions_observed": {"restored": False}})
    assert device.cleanup_completed(job.folder, "eval") is False

    device.write_json(job.folder / "result.json", report)
    device.write_json(
        job.folder / "foreground.json", {**foreground, "restored": False, "restore_exit_code": 1}
    )
    assert device.cleanup_completed(job.folder, "eval") is False
