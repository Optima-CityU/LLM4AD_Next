"""Exercise collector restoration without running ADB or native executables."""

import importlib
import json
import os
import subprocess

import pytest

from llm4ad.gateway.examples.hgamey.execution import collectors
from llm4ad.gateway.examples.hgamey.protocol.game_ab import load_upstream

pytestmark = pytest.mark.unit
TOKEN = "a" * 32
BOOT = "fixture-boot"
SERIAL = "fixture-physical"
ENDPOINT = "fixture:5555"


def save(path, value):
    """Write synthetic evidence into the fixture directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class Device:
    """Return deterministic identity and exact owned-resource readbacks."""

    def __init__(self):
        """Initialize a connected device with no real transport."""
        self.commands = []
        self.physical = SERIAL
        self.boot = BOOT
        self.stop = True
        self.remove = True
        self.mali_absent = True

    def shell(self, command, **kwargs):
        """Answer only the expected device identity reads."""
        self.commands.append(command)
        if command == "getprop ro.serialno":
            return self.physical
        assert command == "cat /proc/sys/kernel/random/boot_id"
        return self.boot

    def su(self, command, **kwargs):
        """Acknowledge cleanup without executing the supplied shell text."""
        self.commands.append(command)
        if "LLM4AD_PERFETTO_STOPPED" in command:
            return "LLM4AD_PERFETTO_STOPPED\n" if self.stop else ""
        if "LLM4AD_PERFETTO_REMOVED" in command:
            return "LLM4AD_PERFETTO_REMOVED\n" if self.remove else ""
        if "LLM4AD_MALI_ABSENT" in command:
            return "LLM4AD_MALI_ABSENT\n" if self.mali_absent else ""
        raise AssertionError(command)


@pytest.fixture
def job(tmp_path, monkeypatch):
    """Bind a job to an inherited descriptor and a mocked upstream transport."""
    folder = tmp_path / "job"
    save(
        folder / "input.json",
        {
            "device": {"id": "fixture", "endpoint": ENDPOINT, "expected_serial": SERIAL},
            "submission": {"operation": "game-ab", "options": {"package": "fixture.game"}},
        },
    )
    save(folder / "connection.json", {"boot_id": BOOT})
    dev = Device()
    upstream = load_upstream()
    native = importlib.import_module(upstream.PerfettoCapture.__module__)
    monkeypatch.setattr(collectors, "_runtime", lambda endpoint: (dev, native.reconcile))
    with os.fdopen(os.open(os.devnull, os.O_RDONLY), "rb") as lease:
        monkeypatch.setattr(collectors, "inherited_fds", lambda: (lease.fileno(),))
        yield folder, dev, native


def perfetto(folder, **changes):
    """Persist an exact recorder intent before any arm report exists."""
    directory = folder / "evidence/traces/A1_perfetto"
    state = {
        "schema": "llm4ad/perfetto-capture@1",
        "arm": "A1",
        "package": "fixture.game",
        "remote": "/data/local/tmp/llm4ad_perfetto_" + TOKEN,
        "session": "llm4ad_perfetto_" + TOKEN,
        "marker": "llm4ad_window_" + TOKEN,
        "status": "recording",
        "submitted": True,
        "cleanup_confirmed": False,
        "recovery_unknown": True,
        "boot_id": BOOT,
        "capabilities": {"physical_serial": SERIAL, "boot_id": BOOT},
    }
    state.update(changes)
    save(directory / "perfetto-cleanup.json", state)
    return directory


def mali(folder, *, complete=True):
    """Keep a tagged shell cleanup receipt separate from the measurement report."""
    directory = folder / "evidence/traces/A1_mali"
    instance = "/sys/kernel/tracing/instances/llm4ad_mali_" + TOKEN
    temporary = "/data/local/tmp/llm4ad_mali_" + TOKEN
    receipt = {
        "instance": instance,
        "owned": "1",
        "removed": "1",
        "sf_disabled": "1",
        "sampler_stopped": "1",
        "temp_removed": "1",
        "stage": "complete",
        "exit_code": "0",
    }
    state = {
        "cleanup_confirmed": complete,
        "recovery_unknown": not complete,
        "error": "historical transport failure",
        "metrics": {},
        "provenance": {
            "source": "owned_private_ftrace",
            "serial": ENDPOINT,
            "package": "fixture.game",
            "instance": instance,
            "temporary_directory": temporary,
            "cleanup_receipt": receipt,
        },
    }
    save(directory / "mali-cleanup.json", state)
    tag = "LLM4AD_MALI_" + TOKEN
    raw = (
        f"{tag}:identity_before\nuid=123\nboot_id={BOOT}\npid=77 uid=123 start=100\n"
        f"{tag}:cleanup\n" + "\n".join(f"{k}={v}" for k, v in receipt.items()) + f"\n{tag}:done\n"
    )
    (directory / "mali-capture.txt").write_text(raw)
    return directory


def test_perfetto_reconciles_before_arm_report_and_preserves_measurements(job):
    """Discover the original recorder and retain failed measurement bytes."""
    folder, dev, _ = job
    directory = perfetto(folder)
    save(folder / "evidence/report.json", {"status": "ERROR", "reason": "lost transport"})
    original = (folder / "evidence/report.json").read_bytes()
    proofs = collectors.restore_collectors(folder, ENDPOINT)
    assert len(proofs) == 1 and proofs[0]["collector"] == "perfetto"
    assert proofs[0]["cleanup_confirmed"] is True and proofs[0]["recovery_unknown"] is False
    assert proofs[0]["directory"] == "evidence/traces/A1_perfetto"
    state = json.loads((directory / "perfetto-cleanup.json").read_text())
    assert state["recorder_stopped"] and state["staging_removed"]
    assert (folder / "evidence/report.json").read_bytes() == original
    assert not (folder / "evidence/arms.jsonl").exists()
    assert not any("perfetto -D" in command or "killall" in command for command in dev.commands)


@pytest.mark.parametrize("failure", ["stop", "remove", "identity", "boot", "download"])
def test_perfetto_unconfirmed_cleanup_raises_and_retains_remote_data(job, monkeypatch, failure):
    """A stopped recorder or attempted transfer alone cannot release staging."""
    folder, dev, native = job
    directory = perfetto(folder)
    if failure in {"stop", "remove"}:
        setattr(dev, failure, False)
    elif failure == "identity":
        dev.physical = "another-phone"
    elif failure == "boot":
        dev.boot = "another-boot"
    else:
        original_su = dev.su

        def ready(command, **kwargs):
            output = original_su(command, **kwargs)
            return "TRACE_READY\n" + output if "LLM4AD_PERFETTO_STOPPED" in command else output

        monkeypatch.setattr(dev, "su", ready)
        monkeypatch.setattr(
            native,
            "download_root_file",
            lambda *a, **k: (_ for _ in ()).throw(TimeoutError("transfer")),
        )
    with pytest.raises(RuntimeError, match="unconfirmed|identity|boot"):
        collectors.restore_collectors(folder, ENDPOINT)
    if failure in {"stop", "identity", "boot", "download"}:
        assert not any("LLM4AD_PERFETTO_REMOVED" in command for command in dev.commands)
    assert (directory / "perfetto-cleanup.json").exists()


@pytest.mark.parametrize("collector", ["mali", "perfetto"])
def test_empty_collector_directory_is_declared_pre_intent_noop(job, collector):
    """Both producers persist intent after mkdir and before remote mutation."""
    folder, dev, _ = job
    (folder / f"evidence/traces/A1_{collector}").mkdir(parents=True)
    proofs = collectors.restore_collectors(folder, ENDPOINT)
    assert proofs[0]["method"] == "no_device_mutations"
    assert not dev.commands


@pytest.mark.parametrize("collector", ["mali", "perfetto"])
def test_missing_intent_with_progress_is_unconfirmed(job, collector):
    """A populated collector directory cannot be inferred to be unused."""
    folder, dev, _ = job
    path = folder / f"evidence/traces/A1_{collector}/start.txt"
    path.parent.mkdir(parents=True)
    path.write_text("partial progress")
    with pytest.raises(RuntimeError, match="intent"):
        collectors.restore_collectors(folder, ENDPOINT)
    assert not dev.commands


def test_no_collector_directories_is_noop_before_lifecycle_creation(job):
    """The producers cannot submit remote work before creating their directory."""
    folder, dev, _ = job
    assert collectors.restore_collectors(folder, ENDPOINT) == []
    assert not dev.commands


@pytest.mark.parametrize("kind", ["perfetto", "mali_telemetry"])
def test_arm_reference_prevents_missing_directory_noop(job, kind):
    """A historical declaration of a collector cannot disappear from inventory."""
    folder, _, _ = job
    path = folder / "evidence/arms.jsonl"
    path.parent.mkdir()
    path.write_text(json.dumps({"arm": "A1", kind: {"submitted": True}}) + "\n")
    with pytest.raises(RuntimeError, match="missing"):
        collectors.restore_collectors(folder, ENDPOINT)


def test_perfetto_pre_submission_intent_is_noop(job):
    """Explicit submitted=false precedes every remote recorder mutation."""
    folder, dev, _ = job
    perfetto(
        folder, submitted=False, status="pending", cleanup_confirmed=True, recovery_unknown=False
    )
    assert collectors.restore_collectors(folder, ENDPOINT)[0]["method"] == "no_device_mutations"
    assert not dev.commands


@pytest.mark.parametrize("complete", [False, True])
def test_mali_tagged_completion_requires_fresh_absence_and_preserves_history(job, complete):
    """A lost final JSON update can use the original complete shell receipt."""
    folder, dev, _ = job
    directory = mali(folder, complete=complete)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    proofs = collectors.restore_collectors(folder, ENDPOINT)
    assert proofs[0]["collector"] == "mali" and proofs[0]["method"] == "recorded_cleanup"
    assert proofs[0]["cleanup_confirmed"] is True
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}
    assert any("LLM4AD_MALI_ABSENT" in command for command in dev.commands)
    assert not any(
        "kill" in command or "timestats" in command or "rm " in command for command in dev.commands
    )


@pytest.mark.parametrize(
    "failure",
    ["missing_raw", "missing_done", "wrong_tag", "sampler", "sf", "instance", "present", "boot"],
)
def test_mali_unknown_ownership_never_writes_or_kills(job, failure):
    """A private path or cleanup boolean alone cannot authorize process/control writes."""
    folder, dev, _ = job
    directory = mali(folder, complete=False)
    path = directory / "mali-capture.txt"
    raw = path.read_text()
    if failure == "missing_raw":
        path.unlink()
    elif failure == "missing_done":
        path.write_text(raw.replace("LLM4AD_MALI_" + TOKEN + ":done", ""))
    elif failure == "wrong_tag":
        path.write_text(raw.replace(TOKEN, "b" * 32))
    elif failure == "sampler":
        path.write_text(raw.replace("sampler_stopped=1", "sampler_stopped=0"))
    elif failure == "sf":
        path.write_text(raw.replace("sf_disabled=1", "sf_disabled=0"))
    elif failure == "instance":
        path.write_text(raw.replace("instance=/sys/kernel/tracing/instances/", "instance=/wrong/"))
    elif failure == "present":
        dev.mali_absent = False
    else:
        path.write_text(raw.replace(BOOT, "wrong-boot"))
    with pytest.raises(RuntimeError, match="Mali|boot"):
        collectors.restore_collectors(folder, ENDPOINT)
    assert not any(
        "kill" in command or "rm " in command or "timestats" in command for command in dev.commands
    )


def test_mali_reserved_intent_is_noop(job):
    """The reservation is replaced with uncertain state before submission."""
    folder, dev, _ = job
    save(
        folder / "evidence/traces/A1_mali/mali-cleanup.json",
        {
            "instance": "/sys/kernel/tracing/instances/llm4ad_mali_" + TOKEN,
            "temporary_directory": "/data/local/tmp/llm4ad_mali_" + TOKEN,
            "submitted": False,
        },
    )
    assert collectors.restore_collectors(folder, ENDPOINT)[0]["method"] == "no_device_mutations"
    assert not dev.commands


@pytest.mark.parametrize(
    "mutation", ["schema", "submitted", "session", "remote", "serial", "package", "arm"]
)
def test_invalid_perfetto_intent_never_reaches_reconciler(job, mutation):
    """Preflight ownership and strict booleans before upstream shell interpolation."""
    folder, dev, _ = job
    directory = perfetto(folder)
    path = directory / "perfetto-cleanup.json"
    state = json.loads(path.read_text())
    if mutation == "serial":
        state["capabilities"]["physical_serial"] = "foreign"
    else:
        state[mutation] = "invalid"
    save(path, state)
    with pytest.raises(RuntimeError, match="intent|identity"):
        collectors.restore_collectors(folder, ENDPOINT)
    assert not dev.commands


@pytest.mark.parametrize("part", ["directory", "receipt"])
def test_collector_symlinks_are_rejected(job, tmp_path, part):
    """A collector must not redirect cleanup or receipt writes outside its job."""
    folder, dev, _ = job
    directory = perfetto(folder)
    target = directory if part == "directory" else directory / "perfetto-cleanup.json"
    destination = tmp_path / "outside"
    target.rename(destination)
    target.symlink_to(destination, target_is_directory=part == "directory")
    with pytest.raises(RuntimeError, match="symlink"):
        collectors.restore_collectors(folder, ENDPOINT)
    assert not dev.commands


def test_lease_and_endpoint_are_required_before_reconciliation(job, monkeypatch):
    """The helper never acquires a new lease or targets a different endpoint."""
    folder, dev, _ = job
    perfetto(folder)
    with pytest.raises(RuntimeError, match="endpoint"):
        collectors.restore_collectors(folder, "other:5555")
    monkeypatch.setattr(collectors, "inherited_fds", lambda: ())
    with pytest.raises(RuntimeError, match="lease"):
        collectors.restore_collectors(folder, ENDPOINT)
    assert not dev.commands


def test_pending_perfetto_does_not_discard_existing_trace(job, monkeypatch):
    """Upstream recovery retains incomplete data instead of replacing or discarding it."""
    folder, dev, native = job
    directory = perfetto(folder)
    trace = directory / "trace.pftrace"
    trace.write_bytes(b"historical partial bytes")
    old = dev.su
    monkeypatch.setattr(dev, "su", lambda command, **kw: "TRACE_READY\n" + old(command, **kw))
    monkeypatch.setattr(
        native,
        "download_root_file",
        lambda *a, **k: (_ for _ in ()).throw(FileExistsError("retained")),
    )
    with pytest.raises(RuntimeError, match="unconfirmed"):
        collectors.restore_collectors(folder, ENDPOINT)
    assert trace.read_bytes() == b"historical partial bytes"
    assert not any("LLM4AD_PERFETTO_REMOVED" in command for command in dev.commands)


def test_completed_perfetto_reuses_bound_completion_without_restarting(job):
    """A completed cleanup record is checked against the original phone and boot."""
    folder, dev, _ = job
    directory = perfetto(
        folder,
        cleanup_confirmed=True,
        recovery_unknown=False,
        staging_removed=True,
        recorder_stopped=True,
        status="captured",
    )
    original = (directory / "perfetto-cleanup.json").read_bytes()
    proof = collectors.restore_collectors(folder, ENDPOINT)[0]
    assert proof["recovery"]["staging_removed"] is True
    assert (directory / "perfetto-cleanup.json").read_bytes() == original
    assert dev.commands == ["getprop ro.serialno", "cat /proc/sys/kernel/random/boot_id"]


@pytest.mark.parametrize("operation", ["eval", "gray-loadop", "probe", "future-mode"])
def test_absent_inventory_requires_known_producer_lifecycle(job, operation):
    """Only current operations can prove missing collector intents mean no work."""
    folder, dev, _ = job
    path = folder / "input.json"
    frozen = json.loads(path.read_text())
    frozen["submission"]["operation"] = operation
    save(path, frozen)
    if operation == "future-mode":
        with pytest.raises(RuntimeError, match="lifecycle"):
            collectors.restore_collectors(folder, ENDPOINT)
    else:
        assert collectors.restore_collectors(folder, ENDPOINT) == []
    assert not dev.commands


@pytest.mark.parametrize("contents", ["{", "[]", "null"])
def test_malformed_later_intent_prevents_any_native_mutation(job, contents):
    """Preflight the whole inventory before restoring its first recorder."""
    folder, dev, _ = job
    perfetto(folder)
    path = folder / "evidence/traces/B1_perfetto/perfetto-cleanup.json"
    path.parent.mkdir(parents=True)
    path.write_text(contents)
    with pytest.raises(RuntimeError, match="intent|unconfirmed"):
        collectors.restore_collectors(folder, ENDPOINT)
    assert not dev.commands


def test_mali_boolean_without_original_closure_is_unconfirmed(job):
    """Persisted cleanup flags alone cannot manufacture sampler/control proof."""
    folder, dev, _ = job
    directory = mali(folder)
    (directory / "mali-capture.txt").unlink()
    with pytest.raises(RuntimeError, match="Mali.*unconfirmed"):
        collectors.restore_collectors(folder, ENDPOINT)
    assert not dev.commands


def test_mali_readback_timeout_is_unconfirmed(job, monkeypatch):
    """A transport timeout is exposed through the helper's cleanup error contract."""
    folder, dev, _ = job
    mali(folder)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("fixture readback", 15)

    monkeypatch.setattr(dev, "su", timeout)
    with pytest.raises(RuntimeError, match="unconfirmed"):
        collectors.restore_collectors(folder, ENDPOINT)
