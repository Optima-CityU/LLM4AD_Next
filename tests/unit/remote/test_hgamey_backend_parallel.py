"""Verify backend device leases and registry transactions without executing phonefarm."""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm4ad.gateway.examples.hgamey.execution import backend

pytestmark = [pytest.mark.unit, pytest.mark.skipif(os.name == "nt", reason="Native device leases require POSIX")]


def job(root, index):
    """Create a distinct synthetic frozen device job with its physical and wireless aliases."""
    folder = root / "jobs" / str(index)
    folder.mkdir(parents=True)
    device = {
        "id": f"phone{index}", "label": f"Synthetic phone {index}",
        "expected_serial": f"physical-{index}", "endpoint": f"192.0.2.{index}:5555",
    }
    backend.write_json(folder / "input.json", {"device": device, "submission": {"operation": "probe"}})
    return folder, device


def entry(device):
    """Return the exact native registry entry, including both physical and wireless aliases."""
    return {
        "device_id": device["id"], "label": device["label"],
        "aliases": [device["expected_serial"], device["endpoint"]], "enabled": True,
    }


def reply(value, code=0):
    """Return a synthetic native command result."""
    return SimpleNamespace(returncode=code, stdout=json.dumps(value), stderr="")


def test_default_engine_registers_all_enabled_devices_before_starting_workers(tmp_path, monkeypatch):
    """Complete registry synchronization and shared ADB startup before starting any device worker."""
    from llm4ad.gateway.examples.hgamey.execution import adb as transport
    from llm4ad.gateway.examples.hgamey.execution.queue import Engine

    data, config = tmp_path / "executor", tmp_path / "devices.json"
    devices = [
        {
            "id": f"phone{index}", "label": f"Synthetic phone {index}",
            "host": f"192.0.2.{index}", "port": 5555,
            "expected_serial": f"physical-{index}", "enabled": index != 3,
        }
        for index in (1, 2, 3)
    ]
    config.write_text(json.dumps({"version": 1, "devices": devices}))
    registry, events = {"revision": 0, "devices": []}, []

    def command(argv, **kwargs):
        if argv == ["phonefarm", "lease", "devices", "--json"]:
            return reply(registry)
        assert argv[:3] == ["phonefarm", "lease", "devices-set"], "An idle engine must never launch a device task"
        request_path = Path(argv[argv.index("--request") + 1])
        request = backend.read_json(request_path)
        device_id = request["device"]["device_id"]
        assert request_path.parent == data / "registry" / device_id
        assert request["expected_revision"] == registry["revision"]
        registry["devices"].append(request["device"])
        registry["revision"] += 1
        events.append(("registered", device_id))
        return reply(registry)

    start = threading.Thread.start

    def start_adb(*args, timeout):
        assert args == ("start-server",) and timeout == 30
        assert {item["device_id"] for item in registry["devices"]} == {"phone1", "phone2"}
        assert [event[0] for event in events] == ["registered", "registered"]
        events.append(("adb_started", None))
        return ""

    def start_worker(thread):
        if thread.name.startswith("phone-worker-"):
            assert {item["device_id"] for item in registry["devices"]} == {"phone1", "phone2"}
            assert ("adb_started", None) in events
            events.append(("worker_started", thread.name))
        return start(thread)

    monkeypatch.setattr(backend, "command", command)
    monkeypatch.setattr(transport, "adb", start_adb)
    monkeypatch.setattr(threading.Thread, "start", start_worker)
    backend.start_runtime(data, config)
    engine = Engine(data, config)
    try:
        assert engine.runner is backend.run_backend
        assert set(engine.executors) == {"phone1", "phone2"}
        assert not hasattr(engine, "threads")
        assert [event[0] for event in events] == ["registered", "registered", "adb_started"]
        assert not (data / "registry/phone3").exists()
        assert not list(engine.jobs.iterdir())
    finally:
        engine.close()


def test_backend_workers_overlap_with_explicit_physical_device_leases(tmp_path, monkeypatch):
    """Different phones reach their independent worker subprocesses concurrently after registration."""
    jobs = [job(tmp_path, index) for index in (1, 2)]
    registry = {"revision": 2, "devices": [entry(device) for _, device in jobs]}
    rendezvous = threading.Barrier(2)
    leases = []

    def command(argv, **kwargs):
        if argv == ["phonefarm", "lease", "devices", "--json"]:
            return reply(registry)
        assert argv[:2] == ["phonefarm", "lease"] and "_device" in argv
        folder = Path(argv[-1])
        device = next(device for path, device in jobs if path == folder)
        assert argv[argv.index("--serial") + 1] == device["expected_serial"]
        assert argv[argv.index("--label") + 1] == "executor:" + folder.name
        assert argv[argv.index("--timeout-ms") + 1] == "30000"
        assert argv.index("--serial") < argv.index("--")
        leases.append(argv)
        rendezvous.wait(timeout=3)
        backend.write_json(folder / "result.json", {"synthetic": True})
        return reply({})

    monkeypatch.setattr(backend, "command", command)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(backend.run_backend, folder) for folder, _ in jobs]
        assert [future.result(timeout=5) for future in futures] == [0, 0]
    assert len(leases) == 2


def test_registry_read_compare_write_is_serialized_across_backend_threads(tmp_path, monkeypatch):
    """A second registrar must not read a revision while the first transaction is still pending."""
    jobs = [job(tmp_path, index) for index in (1, 2)]
    registry = {"revision": 0, "devices": []}
    first_read, second_started, second_read, release = (threading.Event() for _ in range(4))
    reads = []

    def command(argv, **kwargs):
        if argv == ["phonefarm", "lease", "devices", "--json"]:
            snapshot = json.loads(json.dumps(registry))
            reads.append(snapshot["revision"])
            if len(reads) == 1:
                first_read.set()
                assert release.wait(timeout=3)
            else:
                second_read.set()
            return reply(snapshot)
        assert argv[:3] == ["phonefarm", "lease", "devices-set"]
        request = backend.read_json(Path(argv[argv.index("--request") + 1]))
        assert request["expected_revision"] == registry["revision"]
        registry["devices"].append(request["device"])
        registry["revision"] += 1
        return reply(registry)

    def second():
        second_started.set()
        backend.sync_registry(jobs[1][1], jobs[1][0])

    monkeypatch.setattr(backend, "command", command)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(backend.sync_registry, jobs[0][1], jobs[0][0])
        assert first_read.wait(timeout=3)
        other = pool.submit(second)
        try:
            assert second_started.wait(timeout=3)
            assert not second_read.wait(timeout=0.1)
        finally:
            release.set()
        first.result(timeout=3)
        other.result(timeout=3)
    assert reads == [0, 1] and registry["revision"] == 2
    assert registry["devices"] == [entry(device) for _, device in jobs]


def test_registry_revision_conflict_rereads_before_retry(tmp_path, monkeypatch):
    """An external registrar's revision change requires a fresh CAS, preserving its other device."""
    folder, device = job(tmp_path, 1)
    _, other = job(tmp_path, 2)
    registry = {"revision": 0, "devices": []}
    revisions = []

    def command(argv, **kwargs):
        if argv == ["phonefarm", "lease", "devices", "--json"]:
            return reply(registry)
        request = backend.read_json(Path(argv[argv.index("--request") + 1]))
        revisions.append(request["expected_revision"])
        if len(revisions) == 1:
            registry.update(revision=1, devices=[entry(other)])
            return reply({"code": "conflict", "error": "expected_revision does not match current revision 1; read the registry before any new submission"}, 2)
        assert request["expected_revision"] == registry["revision"]
        registry["devices"].append(request["device"])
        registry["revision"] += 1
        return reply(registry)

    monkeypatch.setattr(backend, "command", command)
    backend.sync_registry(device, folder)
    assert revisions == [0, 1] and registry["devices"] == [entry(other), entry(device)]


@pytest.mark.parametrize("error,attempts", [
    ({"code": "conflict", "error": "expected_revision does not match current revision 1"}, 3),
    ({"code": "conflict", "error": "alias belongs to another device"}, 1),
    ({"code": "invalid_request", "error": "invalid device"}, 1),
])
def test_registration_failures_never_launch_a_device_worker(tmp_path, monkeypatch, error, attempts):
    """Retry only bounded revision conflicts and fail before device access on all registration errors."""
    folder, _ = job(tmp_path, 1)
    writes = []

    def command(argv, **kwargs):
        if argv == ["phonefarm", "lease", "devices", "--json"]:
            return reply({"revision": 0, "devices": []})
        assert argv[:3] == ["phonefarm", "lease", "devices-set"]
        writes.append(argv)
        return reply(error, 2)

    monkeypatch.setattr(backend, "command", command)
    with pytest.raises(backend.BackendNotStartedError, match="device registration failed"):
        backend.run_backend(folder)
    assert len(writes) == attempts
    assert not (folder / "worker.stdout").exists()
