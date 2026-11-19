"""Runtime owns shared ADB startup before any SDK resource worker runs."""

import os
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from llm4ad.gateway.examples.hgamey.execution import adb, backend, process
from llm4ad.gateway.examples.hgamey.execution.storage import write_json

pytestmark = [pytest.mark.unit, pytest.mark.skipif(os.name == "nt", reason="Native phonefarm requires POSIX")]


@pytest.fixture
def devices(tmp_path):
    """Provide two synthetic enabled resources and no real transport."""
    config = tmp_path / "devices.json"
    write_json(config, {"version": 1, "devices": [
        {"id": f"phone{i}", "label": f"Phone {i}", "host": f"192.0.2.{i}", "port": 5555,
         "expected_serial": f"physical-{i}", "enabled": True} for i in (1, 2)
    ]})
    return tmp_path / "executor", config


@pytest.mark.parametrize("configured_binary", [False, True])
def test_runtime_primes_one_unmarked_shared_daemon_after_registering_resources(devices, monkeypatch, configured_binary):
    """A shared ADB service can never inherit an individual SDK slot marker."""
    data, config = devices
    executable = "/synthetic/adb" if configured_binary else "adb"
    if configured_binary:
        monkeypatch.setenv("ADB_BIN", executable)
    else:
        monkeypatch.delenv("ADB_BIN", raising=False)
    monkeypatch.setenv("LLM4AD_PROCESS_OWNER", "other-slot")
    monkeypatch.setenv("LLM4AD_PROCESS_DIRECTORY", "/unrelated-owner")
    events = []
    monkeypatch.setattr(backend, "sync_registry", lambda device, folder: events.append(device["id"]))

    def run(argv, **kwargs):
        assert events == ["phone1", "phone2"]
        assert argv == [executable, "start-server"] and kwargs["timeout"] == 30
        assert kwargs["pass_fds"] == ()
        assert not any(key.startswith("LLM4AD_PROCESS_") for key in kwargs["env"])
        events.append("daemon")
        return SimpleNamespace(returncode=0, stdout="ready", stderr="")

    monkeypatch.setattr(process.subprocess, "run", run)
    backend.start_runtime(data, config)
    assert events == ["phone1", "phone2", "daemon"]


@pytest.mark.parametrize("failure", ["nonzero", "timeout", "missing_binary"])
def test_runtime_bootstrap_failure_cannot_start_sdk_device_work(devices, monkeypatch, failure):
    """Runtime startup fails before a pool can be constructed or a job consumed."""
    data, config = devices
    monkeypatch.setattr(backend, "sync_registry", Mock())

    def run(argv, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if failure == "missing_binary":
            raise FileNotFoundError("synthetic ADB missing")
        return SimpleNamespace(returncode=1, stdout="", stderr="server did not ACK")

    monkeypatch.setattr(process.subprocess, "run", run)
    expected = {"nonzero": RuntimeError, "timeout": subprocess.TimeoutExpired, "missing_binary": FileNotFoundError}[failure]
    with pytest.raises(expected):
        backend.start_runtime(data, config)
    assert not list(data.glob("scheduling/*"))
    assert not list(data.glob("jobs/*"))


def test_adb_client_strips_markers_and_native_lease_descriptors(monkeypatch):
    """Finite task clients cannot accidentally assign their daemon to a slot."""
    monkeypatch.setenv("LLM4AD_PROCESS_OWNER", "slot-owner")
    monkeypatch.setenv("LLM4AD_PROCESS_DIRECTORY", "/slot-directory")
    monkeypatch.setenv("PF_LOCK_FDS", "999")
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="device", stderr=""))
    monkeypatch.setattr(process.subprocess, "run", run)
    assert adb.adb("-s", "192.0.2.1:5555", "get-state") == "device"
    assert run.call_args.kwargs["pass_fds"] == ()
    assert "LLM4AD_PROCESS_OWNER" not in run.call_args.kwargs["env"]
