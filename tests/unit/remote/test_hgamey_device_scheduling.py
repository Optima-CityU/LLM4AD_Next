"""Exercise real SDK resource pools with file barriers and synthetic phone CLIs."""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import pytest

from llm4ad.gateway.examples.hgamey.execution.storage import read_json, write_json

pytestmark = [pytest.mark.unit, pytest.mark.skipif(os.name == "nt", reason="native executor leases require POSIX")]
WAIT = 20
FIXTURE_ENV = "LLM4AD_TEST_DEVICE_SCHEDULING"


def process_alive(identity):
    """Distinguish a live fixture worker from PID reuse and zombies."""
    try:
        process = psutil.Process(identity["pid"])
        return process.create_time() == identity["create_time"] and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def run_scheduling_backend(folder):
    """Wait on files in an importable worker and emit synthetic cleanup evidence.

    Args:
        folder: Original immutable operation directory supplied by the Engine.

    Returns:
        A successful synthetic measurement exit code.
    """
    from llm4ad.gateway.sdk.processes import OWNER_ENV, process_identity

    folder = Path(folder)
    root = Path(os.environ[FIXTURE_ENV])
    frozen = read_json(folder / "input.json")
    gate = root / "barriers" / folder.name
    gate.mkdir(parents=True, exist_ok=True)
    observation = {**process_identity(), "id": folder.name, "device_id": frozen["device"]["id"],
                   "operation": frozen["submission"]["operation"], "entered_at": time.monotonic_ns(),
                   "owner": os.environ[OWNER_ENV]}
    for path in (root / "barriers").glob("*/entered.json"):
        previous = read_json(path)
        if (previous["device_id"] == observation["device_id"]
            and not (path.parent / "exited.json").exists() and process_alive(previous)):
            write_json(root / "overlap.json", {"previous": previous, "current": observation})
            raise AssertionError("Two SDK workers entered the same physical resource")
    write_json(folder / "result.json", {"conditions_observed": {"restored": False}})
    write_json(folder / "foreground.json", {"restore_required": False, "headless": True, "paused": False})
    write_json(gate / "entered.json", observation)
    deadline = time.monotonic() + 60
    while not (gate / "release").exists() and not (root / "release-all").exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Test did not release its synthetic backend")
        time.sleep(0.01)
    write_json(folder / "result.json", {"conditions_observed": {"restored": not (gate / "unrestored").exists()}})
    write_json(gate / "exited.json", {"at": time.monotonic_ns()})
    return 0


class Scheduler:
    """Observe the production Engine through real process-written file receipts."""

    def __init__(self, root, monkeypatch):
        """Prepare synthetic inputs and a process-safe restoration CLI stub."""
        self.root, self.config, self.data = root, root / "devices.json", root / "executor"
        self.engines, self.closed = [], set()
        (root / "barriers").mkdir()
        write_json(self.config, {"version": 1, "devices": [
            {"id": name, "label": name, "host": host, "port": 5555,
             "expected_serial": name + "-serial", "enabled": True}
            for name, host in (("phone-a", "192.0.2.10"), ("phone-b", "192.0.2.11"))]})
        self.shader, self.layer = root / "candidate.comp", root / "layer.so"
        self.shader.write_text("#version 450\nvoid main() {}\n")
        self.layer.write_bytes(b"\x7fELFsynthetic-layer-not-executable")
        tools = root / "tools"
        tools.mkdir()
        phonefarm = tools / "phonefarm"
        phonefarm.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys, time, uuid\n"
            "from pathlib import Path\n"
            "args = sys.argv[1:]\n"
            "assert args[0] == 'lease' and '--' in args, args\n"
            "command = args[args.index('--') + 1:]\n"
            "assert command[1:4] == ['-m', 'llm4ad.gateway.examples.hgamey.execution.executor', '_restore'], command\n"
            "assert os.environ.get('LLM4AD_PROCESS_OWNER'), 'Missing SDK ownership'\n"
            "folder = Path(command[4])\n"
            "frozen = json.loads((folder / 'input.json').read_text())\n"
            "assert args[args.index('--serial') + 1] == frozen['device']['expected_serial']\n"
            "assert args[args.index('--label') + 1] == 'sdk-recovery:' + folder.name\n"
            f"gate = Path(os.environ[{FIXTURE_ENV!r}]) / 'barriers' / folder.name\n"
            "gate.mkdir(parents=True, exist_ok=True)\n"
            "(gate / 'cleanup-entered').touch()\n"
            "while (gate / 'hold-cleanup').exists() and not (gate / 'release-cleanup').exists():\n"
            f"    if (Path(os.environ[{FIXTURE_ENV!r}]) / 'release-all').exists(): break\n"
            "    time.sleep(0.01)\n"
            "proof = {'schema': 'hgamey/sdk-native-restoration@1', 'job_id': folder.name,\n"
            "         'device_id': frozen['device']['id'], 'restored': not (gate / 'unrestored').exists(),\n"
            "         'verified_at': time.monotonic_ns(), 'pid': os.getpid()}\n"
            "path = gate / ('cleanup-' + uuid.uuid4().hex + '.json')\n"
            "temporary = path.with_suffix('.tmp')\n"
            "temporary.write_text(json.dumps(proof))\n"
            "temporary.replace(path)\n"
            "print(json.dumps(proof))\n"
            "sys.exit(0 if proof['restored'] else 2)\n"
        )
        adb = tools / "adb"
        adb.write_text(f"#!{sys.executable}\nraise RuntimeError('Scheduling tests must never contact ADB')\n")
        phonefarm.chmod(0o755)
        adb.chmod(0o755)
        monkeypatch.setenv(FIXTURE_ENV, str(root))
        monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
        monkeypatch.setenv("ADB_BIN", str(adb))

    def open(self):
        """Open real SDK executors with a module-importable backend reference."""
        from llm4ad.gateway.examples.hgamey.execution import Engine

        engine = Engine(self.data, self.config, runner=run_scheduling_backend)
        self.engines.append(engine)
        return engine

    def close(self, engine):
        """Stop one controller and release its exclusive queue leases."""
        engine.close()
        self.closed.add(engine)

    def body(self, device, operation="eval"):
        """Build static or game submissions from temporary synthetic assets."""
        body = {"device_id": device, "operation": operation}
        if operation == "eval":
            body["request"] = {"version": 2, "kind": "shader", "payload": {"shader_path": str(self.shader)}}
        else:
            body["options"] = {"a": "native", "b": "layer", "layer_path": str(self.layer)}
        return body

    def gate(self, job):
        """Return the persistent cross-process barrier directory for one job."""
        path = self.root / "barriers" / job["id"]
        path.mkdir(exist_ok=True)
        return path

    def wait(self, predicate):
        """Pump completions until an observed transition or a bounded deadline."""
        deadline = time.monotonic() + WAIT
        while True:
            for engine in self.engines:
                if engine not in self.closed:
                    engine.poll()
            value = predicate()
            if value:
                return value
            if time.monotonic() >= deadline:
                pytest.fail(f"Timed out waiting for SDK resource transition: {[e.store.records() for e in self.engines]}")
            time.sleep(0.02)

    def started(self, job):
        """Wait for actual backend entry and check its separate live process."""
        path = self.gate(job) / "entered.json"
        self.wait(path.exists)
        identity = read_json(path)
        assert identity["pid"] != os.getpid() and identity["owner"]
        assert process_alive(identity)
        return identity

    def release(self, job):
        """Release exactly one blocked synthetic measurement."""
        (self.gate(job) / "release").touch()

    def entered(self, job):
        """Observe backend admission across pool generations and restarts."""
        return (self.gate(job) / "entered.json").exists()

    def terminal(self, engine, job, state="completed"):
        """Wait for a terminal receipt and its separate durable queue ACK."""
        def committed():
            record = engine.store.get(job["id"])
            return record if record["state"] == state and job["id"] not in engine.executors[job["device_id"]].entries() else None

        return self.wait(committed)

    def calls(self, device=None):
        """Read worker-written admissions in their actual observed order."""
        records = [read_json(path) for path in (self.root / "barriers").glob("*/entered.json")]
        return [r["id"] for r in sorted(records, key=lambda r: r["entered_at"])
                if device is None or r["device_id"] == device]

    def cleanup_calls(self, job):
        """Read actual invocations of the local restoration CLI subprocess."""
        return [read_json(path) for path in self.gate(job).glob("cleanup-*.json")]

    def seed(self, number, device, operation, state, created_at):
        """Persist interrupted and pending operations using real frozen inputs."""
        from llm4ad.gateway.examples.hgamey.execution.devices import load_devices, prepare_submission
        from llm4ad.gateway.examples.hgamey.execution.storage import freeze_shader_inputs
        from llm4ad.gateway.examples.hgamey.protocol.game_ab import freeze_inputs

        key = f"{number:032x}"
        folder = self.data / "jobs" / key
        folder.mkdir(parents=True)
        frozen = prepare_submission(self.body(device, operation), load_devices(self.config))
        freeze_shader_inputs(folder, frozen)
        if operation == "game-ab":
            freeze_inputs(folder, frozen)
        write_json(folder / "input.json", frozen)
        job = {"id": key, "device_id": device, "operation": operation, "state": state,
               "endpoint": frozen["device"]["endpoint"], "created_at": created_at,
               "cleanup_status": "pending", "evidence_directory": str(folder)}
        if state in {"running", "cancel_requested"}:
            job["started_at"] = created_at + 0.5
            write_json(folder / "result.json", {"conditions_observed": {"restored": False}})
            write_json(folder / "foreground.json", {"restore_required": False, "headless": True, "paused": False})
        write_json(folder / "job.json", job)
        return job


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    """Release barriers and stop every real pool, including on assertion failure."""
    fixture = Scheduler(tmp_path, monkeypatch)
    try:
        yield fixture
    finally:
        (fixture.root / "release-all").touch()
        for engine in fixture.engines:
            if engine not in fixture.closed:
                fixture.close(engine)
        assert not (fixture.root / "overlap.json").exists(), "Two operations entered the same physical resource"
        for path in (fixture.root / "barriers").glob("*/entered.json"):
            assert not process_alive(read_json(path)), "A fixture worker survived controller close"


def assert_active(engine, expected):
    """Compare per-phone health with actual admitted measurement processes."""
    health = engine.health()
    assert health["active_jobs"] == expected
    assert health["max_active_jobs"] == 2
    assert health["active_job"] in expected.values() if expected else health["active_job"] is None
    assert set(health["devices"]) == {"phone-a", "phone-b"}
    for device, status in health["devices"].items():
        assert status["active_job"] == expected.get(device)
        assert status["worker_alive"] is True
    return health


def test_different_devices_genuinely_overlap(scheduler):
    """Two distinct worker PIDs enter before either phone barrier is released."""
    engine = scheduler.open()
    first = engine.submit(scheduler.body("phone-a"))
    first_worker = scheduler.started(first)
    second = engine.submit(scheduler.body("phone-b", "game-ab"))
    second_worker = scheduler.started(second)
    assert first_worker["pid"] != second_worker["pid"]
    assert first_worker["owner"] != second_worker["owner"]
    assert process_alive(first_worker) and process_alive(second_worker)
    assert assert_active(engine, {"phone-a": first["id"], "phone-b": second["id"]})["status"] == "ok"
    scheduler.release(first)
    scheduler.terminal(engine, first)
    assert not process_alive(first_worker) and process_alive(second_worker)
    assert_active(engine, {"phone-b": second["id"]})
    scheduler.release(second)
    scheduler.terminal(engine, second)
    assert_active(engine, {})


@pytest.mark.parametrize("operations", [("eval", "game-ab", "eval"), ("game-ab", "eval", "game-ab")])
def test_same_device_is_fifo_across_static_and_game(scheduler, operations):
    """Static and game operations share one FIFO across real SDK pool generations."""
    engine = scheduler.open()
    first = engine.submit(scheduler.body("phone-a", operations[0]))
    scheduler.started(first)
    second = engine.submit(scheduler.body("phone-a", operations[1]))
    third = engine.submit(scheduler.body("phone-a", operations[2]))
    assert engine.get(second["id"])["state"] == engine.get(third["id"])["state"] == "queued"
    assert not scheduler.entered(second) and not scheduler.entered(third)
    assert assert_active(engine, {"phone-a": first["id"]})["devices"]["phone-a"]["queued"] == 2
    scheduler.release(first)
    scheduler.started(second)
    scheduler.terminal(engine, first)
    assert not scheduler.entered(third)
    scheduler.release(second)
    scheduler.started(third)
    scheduler.terminal(engine, second)
    assert scheduler.calls() == [first["id"], second["id"], third["id"]]
    scheduler.release(third)
    scheduler.terminal(engine, third)


def test_failed_cleanup_is_terminal_and_automatic_probe_reopens_only_its_phone(scheduler):
    """Bounded cleanup failure fences one phone until an automatic probe verifies recovery."""
    engine = scheduler.open()
    failed = engine.submit(scheduler.body("phone-a", "game-ab"))
    scheduler.started(failed)
    queued = engine.submit(scheduler.body("phone-a"))
    (scheduler.gate(failed) / "unrestored").touch()
    scheduler.release(failed)
    scheduler.terminal(engine, failed, "failed")
    scheduler.terminal(engine, queued, "failed")
    assert not scheduler.entered(queued)
    record = engine.get(failed["id"])
    assert record["error_type"] == "NativeCleanupExhausted" and record["cleanup_attempts"] == 3
    healthy = engine.submit(scheduler.body("phone-b"))
    healthy_worker = scheduler.started(healthy)
    health = assert_active(engine, {"phone-b": healthy["id"]})
    assert health["devices"]["phone-a"]["status"] == "recovering"
    assert health["devices"]["phone-b"]["status"] == "ok"
    before = len(scheduler.cleanup_calls(failed))
    assert before >= 3
    (scheduler.gate(failed) / "unrestored").unlink()
    scheduler.terminal(engine, failed, "cancelled")
    recovered = engine.get(failed["id"])
    assert recovered["cleanup_probes"] >= 1 and recovered["cleanup_status"] == "completed"
    assert len(scheduler.cleanup_calls(failed)) > before
    assert engine.health()["devices"]["phone-a"]["status"] == "ok"
    assert process_alive(healthy_worker)
    fresh = engine.submit(scheduler.body("phone-a"))
    scheduler.started(fresh)
    assert scheduler.calls().count(failed["id"]) == 1
    scheduler.release(fresh)
    scheduler.release(healthy)
    scheduler.terminal(engine, fresh)
    scheduler.terminal(engine, healthy)


@pytest.mark.parametrize("interrupted_state", ["running", "cancel_requested"])
def test_restart_restores_interrupted_work_without_replaying_its_measurement(scheduler, interrupted_state):
    """Recovery verifies original cleanup before admitting pending native operations."""
    original = scheduler.seed(1, "phone-a", "game-ab", interrupted_state, 10)
    following = scheduler.seed(2, "phone-a", "eval", "queued", 20)
    later = scheduler.seed(3, "phone-b", "game-ab", "queued", 40)
    earlier = scheduler.seed(4, "phone-b", "eval", "queued", 30)
    evidence = Path(original["evidence_directory"]) / "original-evidence.txt"
    evidence.write_text("original interrupted evidence")
    original_inputs = (evidence.parent / "input.json").read_bytes()
    engine = scheduler.open()
    worker = scheduler.started(following)
    scheduler.started(earlier)
    assert engine.get(original["id"])["cleanup_status"] == "completed"
    proofs = scheduler.cleanup_calls(original)
    assert proofs and all(proof["verified_at"] < worker["entered_at"] for proof in proofs)
    assert not scheduler.entered(original)
    assert evidence.read_text() == "original interrupted evidence"
    assert (evidence.parent / "input.json").read_bytes() == original_inputs
    scheduler.release(following)
    scheduler.release(earlier)
    scheduler.started(later)
    scheduler.release(later)
    for item in (following, earlier, later):
        scheduler.terminal(engine, item)
    assert scheduler.calls("phone-b") == [earlier["id"], later["id"]]


def test_cancelling_running_and_queued_jobs_does_not_affect_another_phone(scheduler):
    """Targeted cancellation kills one generation while another phone retains its FIFO."""
    engine = scheduler.open()
    first = engine.submit(scheduler.body("phone-a"))
    first_worker = scheduler.started(first)
    removed = engine.submit(scheduler.body("phone-a", "game-ab"))
    other = engine.submit(scheduler.body("phone-b", "game-ab"))
    other_worker = scheduler.started(other)
    following = engine.submit(scheduler.body("phone-b"))
    assert engine.cancel(removed["id"])["state"] == "cancelled"
    assert process_alive(first_worker) and process_alive(other_worker)
    assert not scheduler.cleanup_calls(removed)
    assert engine.cancel(first["id"])["cleanup_status"] == "completed"
    assert not process_alive(first_worker) and process_alive(other_worker)
    assert not (scheduler.gate(first) / "release").exists()
    assert scheduler.cleanup_calls(first)
    assert engine.get(following["id"])["state"] == "queued"
    assert scheduler.terminal(engine, first, "cancelled")["cleanup_status"] == "completed"
    health = assert_active(engine, {"phone-b": other["id"]})
    assert health["blocked_jobs"] == []
    assert health["devices"]["phone-a"]["queued"] == 0
    assert health["devices"]["phone-b"]["queued"] == 1
    assert not scheduler.entered(removed)
    scheduler.release(other)
    scheduler.started(following)
    scheduler.terminal(engine, other)
    scheduler.release(following)
    scheduler.terminal(engine, following)
    assert scheduler.calls("phone-b") == [other["id"], following["id"]]
    assert scheduler.calls("phone-a") == [first["id"]]


@pytest.mark.parametrize("restored", [True, False])
def test_slow_cancellation_fences_only_its_own_phone(scheduler, restored):
    """Other phones progress while the cancelled phone retains its cleanup fence."""
    engine = scheduler.open()
    first = engine.submit(scheduler.body("phone-a"))
    scheduler.started(first)
    queued = engine.submit(scheduler.body("phone-a"))
    other = engine.submit(scheduler.body("phone-b"))
    scheduler.started(other)
    following = engine.submit(scheduler.body("phone-b"))
    gate = scheduler.gate(first)
    (gate / "hold-cleanup").touch()
    if not restored:
        (gate / "unrestored").touch()

    def independent_work():
        """Exercise reads, admission, and actual dispatch on the second phone."""
        assert engine.get(other["id"])["state"] == "running"
        added = engine.submit(scheduler.body("phone-a"))
        scheduler.release(other)
        scheduler.started(following)
        scheduler.terminal(engine, other)
        return added

    with ThreadPoolExecutor(max_workers=2) as threads:
        cancellation = threads.submit(engine.cancel, first["id"])
        try:
            deadline = time.monotonic() + WAIT
            while not (gate / "cleanup-entered").exists():
                assert time.monotonic() < deadline, "Cancellation never entered restoration"
                time.sleep(0.01)
            independent = threads.submit(independent_work)
            added = independent.result(timeout=5)
            assert not cancellation.done()
            assert not scheduler.entered(queued) and not scheduler.entered(added)
            assert first["id"] in engine.executors["phone-a"].entries()
        finally:
            (gate / "release-cleanup").touch()
            cancellation.result(timeout=WAIT)

    if not restored:
        assert engine.store.get(first["id"])["cleanup_status"] == "uncertain"
        assert not scheduler.entered(queued)
        (gate / "unrestored").unlink()
    scheduler.started(queued)
    scheduler.release(queued)
    scheduler.terminal(engine, queued)
    scheduler.started(added)
    scheduler.release(added)
    scheduler.terminal(engine, added)
    scheduler.release(following)
    scheduler.terminal(engine, following)


def test_close_stops_active_slots_and_persists_queued_jobs_for_restart(scheduler):
    """Close stops workers and retains immutable pending deliveries for a new controller."""
    engine = scheduler.open()
    active = [engine.submit(scheduler.body(device)) for device in ("phone-a", "phone-b")]
    identities = [scheduler.started(job) for job in active]
    queued = [engine.submit(scheduler.body(device, "game-ab")) for device in ("phone-a", "phone-b")]
    pending_inputs = {job["id"]: (Path(job["evidence_directory"]) / "input.json").read_bytes() for job in queued}
    scheduler.close(engine)
    assert all(not process_alive(identity) for identity in identities)
    with pytest.raises(RuntimeError, match="stopping"):
        engine.submit(scheduler.body("phone-b"))
    for job in queued:
        folder = Path(job["evidence_directory"])
        assert read_json(folder / "job.json") == job
        assert (folder / "input.json").read_bytes() == pending_inputs[job["id"]]
        assert not scheduler.entered(job)
    restarted = scheduler.open()
    for job in queued:
        worker = scheduler.started(job)
        previous = next(item for item in active if item["device_id"] == job["device_id"])
        assert restarted.get(previous["id"])["cleanup_status"] == "completed"
        proofs = scheduler.cleanup_calls(previous)
        assert proofs and all(proof["verified_at"] < worker["entered_at"] for proof in proofs)
    assert_active(restarted, {job["device_id"]: job["id"] for job in queued})
    for job in queued:
        scheduler.release(job)
        scheduler.terminal(restarted, job)
    assert sorted(scheduler.calls()) == sorted(job["id"] for job in active + queued)
