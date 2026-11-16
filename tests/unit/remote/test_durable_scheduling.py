"""Real local worker failures, detached descendants and durable receipt recovery."""

import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import psutil
import pytest
from loky.process_executor import TerminatedWorkerError

from llm4ad.gateway.sdk import processes
from llm4ad.gateway.sdk.scheduling import DurableExecutor

pytestmark = pytest.mark.unit


def _append(path, value):
    """Record an actual worker invocation in an isolated test directory."""
    with Path(path).open("a") as stream:
        stream.write(value + "\n")
    return value


def _wait(ready, release):
    """Expose the actual worker identity and wait for a test-owned release file."""
    Path(ready).write_text(json.dumps(processes.process_identity()))
    deadline = time.monotonic() + 30
    while not Path(release).exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    return "released"


def _orphan(path, crash):
    """Launch a detached subprocess and optionally crash its real loky worker."""
    child_code = (
        "import json,pathlib,psutil,sys,time; p=psutil.Process(); "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps(dict(pid=p.pid,create_time=p.create_time()))); "
        "time.sleep(60)"
    )
    script = (
        "import json,os,pathlib,subprocess,sys,time; "
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r},sys.argv[1]],start_new_session=True); "
        "deadline=time.monotonic()+10; "
        "\nwhile not pathlib.Path(sys.argv[1]).exists() and time.monotonic()<deadline: time.sleep(.01)"
    )
    processes.owned_command([sys.executable, "-c", script, path], check=True, timeout=15)
    if crash:
        os._exit(19)
    time.sleep(30)


def _until(predicate, timeout=15):
    """Wait on observed state with a bounded test deadline."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("Timed out waiting for a test-owned process transition")
        time.sleep(0.02)


def _alive(identity):
    """Distinguish a surviving fixture process from PID reuse or a zombie."""
    try:
        process = psutil.Process(identity["pid"])
        return (
            process.create_time() == identity["create_time"] and process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        )
    except psutil.NoSuchProcess:
        return False


def test_packets_are_json_deduplicated_and_completion_reserves_capacity(tmp_path):
    """One finished Future cannot admit another key until its durable caller ACK."""
    executor = DurableExecutor(tmp_path / "controller")
    output = tmp_path / "calls"
    try:
        first = executor.submit("one", _append, (str(output), "one"))
        assert executor.submit("one", _append, (str(output), "one")) is first
        second = executor.submit("two", _append, (str(output), "two"))
        assert not output.exists()
        assert executor.entries()["one"]["target"].endswith(":_append")
        json.dumps(executor.entries(), allow_nan=False)
        executor.poll()
        assert first.result(timeout=15) == "one"
        owners = list((executor.directory / "owners").glob("*/owner.json"))
        assert len(owners) == 1
        assert executor.worker_identity("one")["create_time"] > 0
        executor.poll()
        assert not second.done()
        executor.ack("one")
        assert not owners[0].parent.exists()
        executor.poll()
        assert second.result(timeout=15) == "two"
        executor.ack("two")
        assert executor.entries() == {}
        assert executor.future("one") is None
        assert output.read_text().splitlines() == ["one", "two"]
    finally:
        executor.close()


def test_recovered_entries_can_be_pruned_before_any_launch(tmp_path):
    """A restart retains delivery identity and leaves journal reconciliation in charge."""
    directory = tmp_path / "controller"
    output = tmp_path / "calls"
    executor = DurableExecutor(directory)
    executor.submit("stale", _append, (str(output), "stale"))
    executor.submit("current", _append, (str(output), "current"))
    executor.close()
    recovered = DurableExecutor(directory)
    try:
        assert set(recovered.entries()) == {"stale", "current"}
        recovered.discard("stale")
        first = recovered.future("current")
        assert recovered.submit("current", _append, (str(output), "current")) is first
        recovered.paused = True
        recovered.poll()
        assert not output.exists()
        recovered.paused = False
        recovered.poll()
        assert first.result(timeout=15) == "current"
        recovered.ack("current")
        assert output.read_text().splitlines() == ["current"]
    finally:
        recovered.close()
    final = DurableExecutor(directory)
    assert final.entries() == {}
    final.close()


def test_completed_unacked_delivery_is_discardable_after_restart(tmp_path):
    """The durable caller can retire a terminal journal row without replaying it."""
    output = tmp_path / "calls"
    directory = tmp_path / "controller"
    executor = DurableExecutor(directory)
    future = executor.submit("done", _append, (str(output), "one"))
    executor.poll()
    assert future.result(timeout=15) == "one"
    identity = executor.worker_identity("done")
    owner = next((directory / "owners").glob("*/owner.json")).parent
    executor.close()
    assert owner.exists()
    recovered = DurableExecutor(directory)
    try:
        assert owner.exists()
        assert recovered.worker_identity("done") == identity
        recovered.ack("done")
        assert not owner.exists()
        recovered.poll()
        assert output.read_text().splitlines() == ["one"]
    finally:
        recovered.close()


def test_invalid_or_conflicting_packet_is_rejected_without_spawning(tmp_path):
    """Only frozen JSON inputs and importable targets may become durable deliveries."""
    executor = DurableExecutor(tmp_path / "controller")
    try:
        retained = executor.submit("same", math.sqrt, (4,))
        with pytest.raises(ValueError, match="Conflicting"):
            executor.submit("same", math.sqrt, (9,))
        with pytest.raises(ValueError, match="importable"):
            executor.submit("closure", lambda: None)
        with pytest.raises(TypeError):
            executor.submit("object", math.sqrt, (object(),))
        with pytest.raises(ValueError):
            executor.submit("nonfinite", math.sqrt, (float("nan"),))
        with pytest.raises(ValueError):
            executor.submit("kwargs", math.sqrt, (4,), kwargs=[])
        assert set(executor.entries()) == {"same"}
        assert executor.future("same") is retained
        executor.discard("same")
    finally:
        executor.close()


def test_cancelling_one_slot_keeps_the_other_slot_and_receipt(tmp_path):
    """Slot termination neither kills another worker nor acknowledges cancellation."""
    executor = DurableExecutor(tmp_path / "controller", concurrency=2)
    first_ready, second_ready = tmp_path / "first", tmp_path / "second"
    release = tmp_path / "release"
    try:
        first = executor.submit("first", _wait, (str(first_ready), str(release)))
        second = executor.submit("second", _wait, (str(second_ready), str(release)))
        third = executor.submit("third", math.sqrt, (9,))
        executor.poll()
        _until(lambda: first_ready.exists() and second_ready.exists())
        identity = json.loads(first_ready.read_text())
        other = json.loads(second_ready.read_text())
        assert executor.cancel("first")
        assert first.cancelled() and not _alive(identity)
        assert _alive(other) and not second.done()
        assert "first" in executor.entries()
        executor.poll()
        assert not third.done()
        executor.ack("first")
        executor.poll()
        assert third.result(timeout=15) == 3
        assert _alive(other) and not second.done()
        release.touch()
        assert second.result(timeout=15) == "released"
        executor.ack("second")
        executor.ack("third")
    finally:
        release.touch()
        executor.close()


def test_pending_cancellation_requires_explicit_ack_and_never_launches(tmp_path):
    """A pending cancellation retains an unacknowledged receipt without executing."""
    executor = DurableExecutor(tmp_path / "controller")
    output = tmp_path / "calls"
    try:
        future = executor.submit("pending", _append, (str(output), "bad"))
        assert future.cancel()
        assert "pending" in executor.entries()
        executor.poll()
        assert not output.exists()
        executor.ack("pending")
        assert executor.entries() == {}
    finally:
        executor.close()


def test_shrinking_capacity_counts_reserved_slots_outside_the_new_index_range(tmp_path):
    """Live capacity changes must drain older slots before admitting another delivery."""
    executor = DurableExecutor(tmp_path / "controller", concurrency=2)
    try:
        first = executor.submit("first", math.sqrt, (4,))
        second = executor.submit("second", math.sqrt, (9,))
        third = executor.submit("third", math.sqrt, (16,))
        executor.poll()
        assert first.result(timeout=15) == 2
        assert second.result(timeout=15) == 3
        executor.concurrency = 1
        executor.ack("first")
        executor.poll()
        assert not third.done()
        executor.ack("second")
        executor.poll()
        assert third.result(timeout=15) == 4
        executor.ack("third")
    finally:
        executor.close()


def test_real_worker_exit_cleans_detached_descendant(tmp_path):
    """A real os._exit cannot leave the detached command alive after Future failure."""
    executor = DurableExecutor(tmp_path / "controller")
    orphan = tmp_path / "orphan"
    try:
        future = executor.submit("crash", _orphan, (str(orphan), True))
        executor.poll()
        with pytest.raises(TerminatedWorkerError):
            future.result(timeout=20)
        identity = json.loads(orphan.read_text())
        assert not _alive(identity)
        assert "crash" in executor.entries()
        executor.ack("crash")
    finally:
        executor.close()


def test_controller_crash_reconciles_orphans_before_recovery(tmp_path):
    """A fresh controller kills the previous marker generation before resuming claims."""
    directory, orphan = tmp_path / "controller", tmp_path / "orphan"
    script = (
        "import os,pathlib,sys,time; "
        "from llm4ad.gateway.sdk.scheduling import DurableExecutor; "
        "from test_durable_scheduling import _orphan; "
        "e=DurableExecutor(sys.argv[1]); e.submit('orphan',_orphan,(sys.argv[2],False)); e.poll(); "
        "deadline=time.monotonic()+15; "
        "\nwhile not pathlib.Path(sys.argv[2]).exists() and time.monotonic()<deadline: time.sleep(.02)"
        "\nos._exit(71 if pathlib.Path(sys.argv[2]).exists() else 72)"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).parent), str(Path.cwd() / "src"), environment.get("PYTHONPATH", "")]
    )
    controller = subprocess.Popen([sys.executable, "-c", script, str(directory), str(orphan)], env=environment)
    recovered = None
    try:
        assert controller.wait(timeout=25) == 71
        identity = json.loads(orphan.read_text())
        recovered = DurableExecutor(directory)
        assert not _alive(identity)
        assert set(recovered.entries()) == {"orphan"}
        recovered.discard("orphan")
        recovered.poll()
    finally:
        if controller.poll() is None:
            controller.kill()
            controller.wait(timeout=5)
        if recovered is not None:
            recovered.close()
        else:
            for owner in (directory / "owners").glob("*/owner.json"):
                processes.cleanup_owner(owner.parent)


def test_owned_command_records_markers_and_actual_process_identity(tmp_path):
    """The wrapper preserves subprocess.run behavior and writes observable PID receipts."""
    directory = tmp_path / "owner"
    result = processes.owned_command(
        [sys.executable, "-c", "import os,time; print(os.environ['LLM4AD_PROCESS_OWNER']); time.sleep(.2)"],
        owner_directory=directory,
        check=True,
        capture_output=True,
        text=True,
    )
    owner = json.loads((directory / "owner.json").read_text())
    assert result.stdout.strip() == owner["nonce"]
    receipt = json.loads(next((directory / "commands").glob("*.json")).read_text())
    assert receipt["finished"] is True
    assert receipt["processes"] and all(item["pid"] and item["create_time"] for item in receipt["processes"])
    processes.cleanup_owner(directory)


def test_unreadable_environment_prevents_resume_and_cleanup_confirmation(tmp_path, monkeypatch):
    """AccessDenied is uncertainty, even when no readable matching process was found."""
    directory = tmp_path / "controller"
    owner = directory / "owners" / "generation"
    processes.create_owner(owner)
    denied = Mock(pid=987654)
    denied.create_time.return_value = time.time()
    denied.status.return_value = psutil.STATUS_SLEEPING
    denied.username.return_value = psutil.Process().username()
    denied.environ.side_effect = psutil.AccessDenied(denied.pid)
    monkeypatch.setattr(processes.psutil, "process_iter", lambda: iter([denied]))
    with pytest.raises(processes.ProcessOwnershipError, match="uncertain"):
        DurableExecutor(directory)
    assert json.loads((owner / "owner.json").read_text())["clean"] is False
    denied.kill.assert_not_called()


def test_old_unreadable_process_cannot_belong_to_a_new_generation(tmp_path, monkeypatch):
    """A process born before the persisted nonce cannot inherit that nonce."""
    directory = tmp_path / "owner"
    owner = processes.create_owner(directory)
    old = Mock(pid=987655)
    old.create_time.return_value = owner["created_at"] - 60
    old.environ.side_effect = psutil.AccessDenied(old.pid)
    monkeypatch.setattr(processes.psutil, "process_iter", lambda: iter([old]))
    processes.cleanup_owner(directory)
    assert json.loads((directory / "owner.json").read_text())["clean"] is True
    old.environ.assert_not_called()
    old.kill.assert_not_called()


def test_stale_pid_receipt_never_kills_a_different_process(tmp_path):
    """An otherwise live PID must match its persisted creation time before termination."""
    directory = tmp_path / "owner"
    owner = processes.create_owner(directory)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        identity = processes.process_identity(psutil.Process(child.pid))
        processes.persist_record(
            directory / "processes" / "stale.json",
            {
                **identity,
                "create_time": identity["create_time"] - 10,
                "nonce": owner["nonce"],
                "role": "worker",
            },
        )
        processes.cleanup_owner(directory)
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=5)
