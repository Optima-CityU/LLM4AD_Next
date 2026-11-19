"""Process ownership receipts replace the retired handwritten guardian protocol."""

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from llm4ad.gateway.sdk import processes

pytestmark = pytest.mark.unit


def test_generation_is_persisted_before_worker_initialization(tmp_path):
    """An ownership generation is durable even if no worker ever starts."""
    directory = tmp_path / "owner"
    before = time.time()
    owner = processes.create_owner(directory, key="job:attempt", slot=2)
    saved = json.loads((directory / "owner.json").read_text())
    assert owner == saved
    assert saved["created_at"] >= before
    assert saved["controller"]["pid"] == os.getpid()
    assert saved["controller"]["create_time"] == psutil.Process().create_time()
    assert saved["slot"] == 2 and saved["clean"] is False
    environment = processes.owner_environment(directory)
    assert environment[processes.OWNER_ENV] == saved["nonce"]
    assert processes.worker_identity(directory) is None
    processes.cleanup_owner(directory)
    assert json.loads((directory / "owner.json").read_text())["clean"] is True


def test_receipt_marker_and_creation_time_identify_only_its_generation(tmp_path, monkeypatch):
    """An exact environment marker is discoverable without trusting parent linkage."""
    directory = tmp_path / "owner"
    owner = processes.create_owner(directory)
    process = Mock(pid=987651)
    process.create_time.return_value = owner["created_at"] + 1
    process.username.return_value = owner["username"]
    process.status.return_value = psutil.STATUS_SLEEPING
    process.environ.return_value = processes.owner_environment(directory)
    monkeypatch.setattr(processes.psutil, "process_iter", lambda: iter([process]))
    assert processes.owner_active(directory)
    process.environ.return_value = {processes.OWNER_ENV: "a-different-generation"}
    assert not processes.owner_active(directory)
    process.kill.assert_not_called()


def test_owned_environment_denial_is_not_absence(tmp_path, monkeypatch):
    """A recent same-user process with unreadable environment keeps cleanup uncertain."""
    directory = tmp_path / "owner"
    owner = processes.create_owner(directory)
    process = Mock(pid=987652)
    process.create_time.return_value = owner["created_at"]
    process.username.return_value = owner["username"]
    process.status.return_value = psutil.STATUS_SLEEPING
    process.environ.side_effect = psutil.AccessDenied(process.pid)
    monkeypatch.setattr(processes.psutil, "process_iter", lambda: iter([process]))
    with pytest.raises(processes.ProcessOwnershipError, match="absence"):
        processes.owner_active(directory)
    with pytest.raises(processes.ProcessOwnershipError, match="uncertain"):
        processes.cleanup_owner(directory, timeout=0.02)
    assert json.loads((directory / "owner.json").read_text())["clean"] is False


def test_tree_active_uses_owned_receipts_without_a_guardian(tmp_path, monkeypatch):
    """The compatibility observation checks a matching job/attempt receipt."""
    journal = SimpleNamespace(path=tmp_path / "jobs.sqlite3")
    directory = tmp_path / "owners" / "generation"
    owner = processes.create_owner(directory, key="job:attempt")
    process = Mock(pid=987653)
    process.create_time.return_value = owner["created_at"]
    process.username.return_value = owner["username"]
    process.status.return_value = psutil.STATUS_SLEEPING
    process.environ.return_value = processes.owner_environment(directory)
    monkeypatch.setattr(processes.psutil, "process_iter", lambda: iter([process]))
    assert processes.tree_active(journal, "job", "attempt")
    assert not processes.tree_active(journal, "another-job", "attempt")


def test_environment_denial_followed_by_verified_exit_is_not_live_uncertainty(tmp_path, monkeypatch):
    """A process exiting between status and environ reads is rechecked, not guessed absent."""
    directory = tmp_path / "owner"
    owner = processes.create_owner(directory)
    process = Mock(pid=987654)
    process.create_time.return_value = owner["created_at"]
    process.username.return_value = owner["username"]
    process.status.side_effect = [psutil.STATUS_SLEEPING, psutil.STATUS_ZOMBIE]
    process.is_running.return_value = True
    process.environ.side_effect = psutil.AccessDenied(process.pid)
    monkeypatch.setattr(processes.psutil, "process_iter", lambda: iter([process]))
    processes.cleanup_owner(directory)
    assert json.loads((directory / "owner.json").read_text())["clean"] is True


def test_cleanup_waits_for_a_fresh_scan_after_transient_inspection_failure(tmp_path, monkeypatch):
    """An unreadable process must actually disappear before cleanup becomes confirmed."""
    directory = tmp_path / "owner"
    owner = processes.create_owner(directory)
    process = Mock(pid=987655)
    process.create_time.return_value = owner["created_at"]
    process.username.return_value = owner["username"]
    process.status.return_value = psutil.STATUS_SLEEPING
    process.is_running.return_value = True
    process.environ.side_effect = psutil.AccessDenied(process.pid)
    scans = iter([[process], []])
    monkeypatch.setattr(processes.psutil, "process_iter", lambda: iter(next(scans)))
    processes.cleanup_owner(directory, timeout=1)
    assert json.loads((directory / "owner.json").read_text())["clean"] is True
    assert process.environ.call_count == 1
    process.kill.assert_not_called()


def test_owned_command_preserves_subprocess_timeout(tmp_path):
    """Ordinary subprocess.run timeout behavior retains durable command ownership."""
    directory = tmp_path / "owner"
    with pytest.raises(subprocess.TimeoutExpired):
        processes.owned_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            owner_directory=directory,
            timeout=0.1,
        )
    receipt = json.loads(next((directory / "commands").glob("*.json")).read_text())
    assert receipt["finished"] is True
    processes.cleanup_owner(directory)
