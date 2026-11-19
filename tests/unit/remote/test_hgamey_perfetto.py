"""Verify owned capture recovery and honest window-scoped analysis without a phone."""

import hashlib
import importlib.util
import json
import os
import re
import shlex
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3] / "src/llm4ad/gateway/examples/hgamey/protocol/upstream/selfsr"


def load(name):
    """Load adjacent helpers without requiring a device or Perfetto installation."""
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(ROOT))
        spec.loader.exec_module(module)
    return module


capture = load("perfetto_capture")
analysis = load("perfetto_analysis")
MARKER = "llm4ad_window_" + "a" * 32


class Device:
    """Simulate transport receipts, keeping all commands inspectable."""

    def __init__(self, start_timeout=False, stop_missing=False, reboot=False):
        """Select a bounded startup or cleanup failure for this fixture."""
        self.commands = []
        self.start_timeout, self.stop_missing, self.reboot = start_timeout, stop_missing, reboot
        self.trace = b"test-only-trace"
        self.reads = []
        self.fail_chunk = None
        self.removal_missing = False

    def su(self, command, timeout=15):
        """Return source content or exact owned recorder receipts."""
        self.commands.append(command)
        if "stat -c '%s'" in command:
            remote = shlex.split(command)[-1]
            return f"fixture-device\nfixture-boot\n{len(self.trace)}\n{hashlib.sha256(self.trace).hexdigest()}  {remote}\n"
        if command.startswith("getprop ro.serialno;"):
            return "fixture-device\nfixture-boot\nfixture-fingerprint\n"
        if command.startswith("perfetto --version"):
            return "Service: Perfetto v46.0\nlinux.ftrace producer\nandroid.power producer\nandroid.surfaceflinger.frametimeline producer\n"
        if command.startswith("service call"):
            return "Result: Parcel(00000000 00000000   '........')\n"
        if "perfetto -D" in command:
            if self.start_timeout:
                raise TimeoutError("lost start reply")
            return "1234\n"
        if "LLM4AD_PERFETTO_STOPPED" in command:
            return "" if self.stop_missing else "TRACE_READY\nLLM4AD_PERFETTO_STOPPED\n"
        if "LLM4AD_PERFETTO_REMOVED" in command:
            return "" if self.removal_missing else "LLM4AD_PERFETTO_REMOVED\n"
        return ""

    def shell(self, command):
        """Retain the original boot identity unless the test requests a reboot."""
        self.commands.append(command)
        if command == "getprop ro.serialno":
            return "fixture-device"
        return "new-boot" if self.reboot else "fixture-boot"

    def adb(self, command, **kwargs):
        """Write only synthetic test evidence to the requested local fixture path."""
        self.commands.append(command)
        parts = shlex.split(command)
        if parts[0] == "pull":
            Path(parts[-1]).write_bytes(self.trace)
            return
        assert parts[:3] == ["exec-out", "su", "-c"] and kwargs["check"] is True
        read = dict(token.split("=", 1) for token in shlex.split(parts[3]) if "=" in token)
        size, skip = int(read["bs"]), int(read["skip"])
        self.reads.append(skip)
        data = self.trace[skip * size:(skip + 1) * size]
        target = Path(parts[-1])
        if skip == self.fail_chunk:
            target.write_bytes(data[:1])
            raise subprocess.TimeoutExpired("synthetic trace download", 60)
        target.write_bytes(data)


def test_powerstats_presence_does_not_advertise_empty_channels(tmp_path):
    """Keep empty AIDL arrays unsupported without guessing usable energy."""
    dev = Device()
    result = capture.probe(dev, tmp_path)
    assert result["perfetto_available"]
    assert result["energy_meters"]["status"] == "unsupported"
    assert result["energy_consumers"]["count"] == 0
    config = capture.configuration(result, "fixture", 10)
    assert "collect_power_rails: false" in config
    assert "collect_energy_estimation_breakdown: false" in config
    assert all("charging_enabled" not in command and "tracing_on" not in command for command in dev.commands)


@pytest.mark.parametrize(
    "reply,status",
    [
        ("Result: Parcel(00000000 00000002 11111111)", "advertised"),
        ("Result: Parcel(ffffffff 00000000)", "unavailable"),
        ("Permission denied", "unavailable"),
    ],
)
def test_binder_reply_retains_unknown_and_advertised_distinction(reply, status):
    """An advertised channel is not a measured energy result."""
    assert capture._info(reply)["status"] == status


def test_owned_start_stop_retains_trace_and_receipt(tmp_path):
    """Capture intent precedes dispatch and a confirmed stop retains exact bytes."""
    dev = Device()
    recorder = capture.Capture(dev, "fixture.game", 2, tmp_path / "capture", "A1")
    assert recorder.start()
    assert recorder.state["recovery_unknown"]
    result = recorder.stop()
    assert result["cleanup_confirmed"] and not result["recovery_unknown"]
    assert result["trace_retrieved"] and result["staging_removed"]
    assert (recorder.output / "trace.pftrace").read_bytes() == b"test-only-trace"
    stop = next(command for command in dev.commands if "LLM4AD_PERFETTO_STOPPED" in command)
    assert "kill -INT" in stop and "killall" not in stop
    assert recorder.state["remote"] in stop and "cmdline" in stop
    assert dev.reads == [0]
    assert not any(command.startswith("pull ") for command in dev.commands)


def test_trace_download_failure_retains_confirmed_stop_and_resumes_original(tmp_path, monkeypatch):
    """A partial download preserves exact bytes and cannot turn a stopped recorder into a live-resource failure."""
    dev = Device()
    dev.fail_chunk = 1
    recorder = capture.Capture(dev, "fixture.game", 2, tmp_path / "capture", "A1")
    assert recorder.start()
    transfer = load("evidence_transfer").download_root_file

    def download(dev, remote, path, **kwargs):
        return transfer(dev, remote, path, **kwargs, chunk_size=4, retries=1)

    monkeypatch.setattr(capture, "download_root_file", download, raising=False)
    state = recorder.state = capture.reconcile(dev, recorder.output)
    assert state["recorder_stopped"] is True and state["recovery_unknown"] is False
    assert state["cleanup_confirmed"] is False
    assert state["status"] == "download_failed" and state["download_error"].startswith("TimeoutExpired:")
    assert state["stop_attempts"] == 1 and not state.get("trace_retrieved")
    assert not state.get("staging_removed")
    assert not any("LLM4AD_PERFETTO_REMOVED" in command for command in dev.commands)
    trace = recorder.output / "trace.pftrace"
    assert not trace.exists() and trace.with_suffix(".pftrace.part").read_bytes() == dev.trace[:4]
    persisted = json.loads((recorder.output / "perfetto-cleanup.json").read_text())
    assert persisted == state
    assert recorder.analyze() == state and "analysis" not in state
    dev.fail_chunk = None
    recovered = capture.reconcile(dev, recorder.output)
    assert recovered["cleanup_confirmed"] is True and recovered["recovery_unknown"] is False
    assert recovered["trace_retrieved"] is True and recovered["staging_removed"] is True
    assert recovered["status"] == "captured" and "download_error" not in recovered
    assert trace.read_bytes() == dev.trace and recovered["trace_sha256"] == hashlib.sha256(dev.trace).hexdigest()
    assert dev.reads == [0, 1, 1, 2, 3]
    assert sum("perfetto -D" in command for command in dev.commands) == 1
    assert all(not command.startswith("pull ") for command in dev.commands)


@pytest.mark.parametrize("receipt", [None, {}, {"complete": False}, {"complete": 1}])
def test_unverified_trace_download_never_removes_staging(tmp_path, monkeypatch, receipt):
    """Retain the owned remote trace when the transfer cannot confirm complete verified bytes."""
    dev = Device()
    recorder = capture.Capture(dev, "fixture.game", 2, tmp_path / "capture", "A1")
    recorder.start()
    monkeypatch.setattr(capture, "download_root_file", lambda *args, **kwargs: receipt, raising=False)
    state = recorder.stop()
    assert state["cleanup_confirmed"] is True and state["recovery_unknown"] is False
    assert state["status"] == "data_unavailable" and state["download_error"]
    assert not state.get("trace_retrieved")
    assert state["trace_discarded"] is True and state["staging_removed"] is True
    assert state["download_attempts"] == 2


@pytest.mark.parametrize("retry_succeeds,removal_missing", [(True, False), (False, False), (False, True)])
def test_stop_bounds_download_retries_and_requires_staging_cleanup(tmp_path, monkeypatch, retry_succeeds, removal_missing):
    """Retry original bytes once, then explicitly discard unavailable remote data with strict cleanup receipts."""
    dev = Device()
    dev.fail_chunk, dev.removal_missing = 1, removal_missing
    recorder = capture.Capture(dev, "fixture.game", 2, tmp_path / "capture", "A1")
    recorder.start()
    transfer = load("evidence_transfer").download_root_file

    def download(dev, remote, path, **kwargs):
        try:
            return transfer(dev, remote, path, **kwargs, chunk_size=4, retries=1)
        except subprocess.TimeoutExpired:
            if retry_succeeds:
                dev.fail_chunk = None
            raise

    monkeypatch.setattr(capture, "download_root_file", download)
    state = recorder.stop()
    assert state["download_attempts"] == 2 and state["recorder_stopped"] is True
    assert state["cleanup_confirmed"] is not removal_missing and state["recovery_unknown"] is removal_missing
    assert bool(state.get("staging_removed")) is not removal_missing
    assert sum("perfetto -D" in command for command in dev.commands) == 1
    assert sum("LLM4AD_PERFETTO_REMOVED" in command for command in dev.commands) == 1
    trace = recorder.output / "trace.pftrace"
    if retry_succeeds:
        assert state["status"] == "captured" and state["trace_retrieved"] is True
        assert trace.read_bytes() == dev.trace and not state.get("trace_discarded")
        assert dev.reads == [0, 1, 1, 2, 3]
    else:
        assert state["status"] == "data_unavailable" and state["trace_discarded"] is True
        assert state["trace_retrieved"] is False and not trace.exists()
        assert trace.with_suffix(".pftrace.part").read_bytes() == dev.trace[:4]
        assert json.loads(trace.with_suffix(".pftrace.transfer.json").read_text())["bytes_complete"] == 4
        assert dev.reads == [0, 1, 1]
        assert "analysis" not in recorder.analyze()


def test_staging_removal_failure_remains_unresolved(tmp_path):
    """Verified data cannot substitute for the explicit device staging cleanup receipt."""
    dev = Device()
    dev.removal_missing = True
    recorder = capture.Capture(dev, "fixture.game", 2, tmp_path / "capture", "A1")
    recorder.start()
    state = recorder.stop()
    assert state["trace_retrieved"] is True
    assert state["cleanup_confirmed"] is False and state["recovery_unknown"] is True
    assert not state.get("staging_removed")


def test_missing_stop_receipt_never_downloads_or_removes_trace(tmp_path):
    """An unresolved recorder remains a cleanup obligation even if trace data could be present."""
    dev = Device(stop_missing=True)
    recorder = capture.Capture(dev, "fixture.game", 2, tmp_path / "capture", "A1")
    recorder.start()
    state = recorder.stop()
    assert state["cleanup_confirmed"] is False and state["recovery_unknown"] is True
    assert dev.reads == []
    assert not any(command.startswith("pull ") or "LLM4AD_PERFETTO_REMOVED" in command for command in dev.commands)


def test_legacy_unverified_pull_file_is_preserved_as_unavailable_data(tmp_path):
    """A legacy partial file cannot be overwritten, analyzed, or mistaken for an active recorder."""
    dev = Device()
    recorder = capture.Capture(dev, "fixture.game", 2, tmp_path / "capture", "A1")
    recorder.start()
    trace = recorder.output / "trace.pftrace"
    trace.write_bytes(b"synthetic legacy incomplete adb pull")
    state = recorder.stop()
    assert state["cleanup_confirmed"] is True and state["recovery_unknown"] is False
    assert state["status"] == "data_unavailable" and state["download_error"].startswith("FileExistsError:")
    assert trace.read_bytes() == b"synthetic legacy incomplete adb pull"
    assert not trace.with_suffix(".pftrace.transfer.json").exists()
    assert not state.get("trace_retrieved") and state["staging_removed"] is True
    assert state["trace_discarded"] is True and state["download_attempts"] == 2
    assert "analysis" not in recorder.analyze()
    assert not dev.reads and sum("LLM4AD_PERFETTO_REMOVED" in command for command in dev.commands) == 1


def test_download_retry_rechecks_original_device_identity(tmp_path, monkeypatch):
    """A previously confirmed stop cannot authorize later evidence cleanup on another phone."""
    dev = Device()
    recorder = capture.Capture(dev, "fixture.game", 2, tmp_path / "capture", "A1")
    recorder.start()
    monkeypatch.setattr(capture, "download_root_file", lambda *args, **kwargs: None)
    assert capture.reconcile(dev, recorder.output)["recorder_stopped"] is True
    monkeypatch.setattr(dev, "shell", lambda command: "another-phone")
    state = capture.reconcile(dev, recorder.output)
    assert state["cleanup_confirmed"] is False and state["recovery_unknown"] is True
    assert "Physical phone changed" in state["cleanup_error"]
    assert not any("LLM4AD_PERFETTO_REMOVED" in command for command in dev.commands)


@pytest.mark.parametrize("identity_line", [0, 1])
def test_transfer_source_must_match_original_capture_identity(tmp_path, monkeypatch, identity_line):
    """A stable transfer from another device or boot cannot authorize trace publication or cleanup."""
    dev = Device()
    recorder = capture.Capture(dev, "fixture.game", 2, tmp_path / "capture", "A1")
    recorder.start()
    original = dev.su

    def changed_source(command, **kwargs):
        raw = original(command, **kwargs)
        if "stat -c '%s'" in command:
            lines = raw.splitlines()
            lines[identity_line] = "different-identity"
            return "\n".join(lines) + "\n"
        return raw

    monkeypatch.setattr(dev, "su", changed_source)
    state = recorder.stop()
    assert state["cleanup_confirmed"] is False and state["recovery_unknown"] is True
    assert "Trace transfer identity differs" in state["cleanup_error"]
    assert not state.get("trace_retrieved")
    assert not any("LLM4AD_PERFETTO_REMOVED" in command for command in dev.commands)


@pytest.mark.skipif(os.name != "posix", reason="Requires a POSIX shell and /dev/zero")
def test_stop_finishes_with_unterminated_process_input(tmp_path):
    """An endless procfs stream cannot leave a CPU-burning filter behind."""
    dev = Device()
    recorder = capture.Capture(dev, "fixture.game", 2, tmp_path / "capture", "A1")
    recorder.start()
    recorder.stop()
    command = next(command for command in dev.commands if "LLM4AD_PERFETTO_STOPPED" in command)
    script = shlex.split(command)[2].replace("/proc/$p/cmdline", "/dev/zero")
    setup = "pidof() { echo 1234; }\nperfetto() { :; }\n"
    # Kill the whole fixture group on failure so a regression cannot leak children.
    process = subprocess.Popen(
        ["sh", "-c", setup + script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
    )
    try:
        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
    assert process.returncode == 0, stderr
    assert stdout.rstrip().endswith(b"LLM4AD_PERFETTO_STOPPED")


@pytest.mark.parametrize("unknown", [False, True])
def test_lost_start_reply_reconciles_only_owned_process(tmp_path, unknown):
    """An uncertain start cannot replay the experiment or clear a missing receipt."""
    dev = Device(start_timeout=True, stop_missing=unknown)
    recorder = capture.Capture(dev, "fixture.game", 2, tmp_path / "capture", "B1")
    assert not recorder.start()
    assert recorder.state["recovery_unknown"] is unknown
    assert recorder.state["cleanup_confirmed"] is not unknown
    assert sum("perfetto -D" in command for command in dev.commands) == 1
    assert json.loads((recorder.output / "perfetto-cleanup.json").read_text())["recovery_unknown"] is unknown


def test_persisted_capture_can_be_reconciled_after_process_restart(tmp_path):
    """Recovery uses the journal's exact boot and command paths, not a new session."""
    dev = Device()
    recorder = capture.Capture(dev, "fixture.game", 2, tmp_path / "capture", "A1")
    recorder.start()
    recovered = capture.reconcile(dev, recorder.output)
    assert recovered["cleanup_confirmed"]
    assert sum("perfetto -D" in command for command in dev.commands) == 1


def test_changed_phone_cannot_confirm_original_recorder_cleanup(tmp_path, monkeypatch):
    """Do not mistake another phone's different boot for the original rebooting."""
    dev = Device()
    recorder = capture.Capture(dev, "fixture.game", 2, tmp_path / "capture", "A1")
    recorder.start()
    monkeypatch.setattr(dev, "shell", lambda command: "another-phone")
    recovered = capture.reconcile(dev, recorder.output)
    assert recovered["recovery_unknown"] and not recovered["cleanup_confirmed"]
    assert not any("kill -INT" in command for command in dev.commands)


@pytest.mark.parametrize("unavailable", ["getprop ro.serialno", "cat /proc/sys/kernel/random/boot_id"])
def test_missing_identity_is_transport_uncertainty_not_a_phone_change(tmp_path, monkeypatch, unavailable):
    """A lost read must not be treated as a reboot or a different device."""
    dev = Device()
    recorder = capture.Capture(dev, "fixture.game", 2, tmp_path / "capture", "A1")
    recorder.start()
    shell = dev.shell
    monkeypatch.setattr(dev, "shell", lambda command: "" if command == unavailable else shell(command))
    recovered = capture.reconcile(dev, recorder.output)
    assert recovered["recovery_unknown"] and not recovered["cleanup_confirmed"]
    assert recovered["cleanup_error"].startswith("ConnectionError:")
    assert not any("kill -INT" in command for command in dev.commands)


def test_stop_timeout_reconciles_exact_owner_without_another_experiment(tmp_path, monkeypatch):
    """Retry only an idempotent owned stop after a lost cleanup response."""
    dev = Device()
    recorder = capture.Capture(dev, "fixture.game", 2, tmp_path / "capture", "A1")
    recorder.start()
    real = dev.su
    stops = []

    def stop_once(command, **kwargs):
        if "LLM4AD_PERFETTO_STOPPED" in command:
            stops.append(command)
            if len(stops) == 1:
                raise subprocess.TimeoutExpired("fixture-stop", 45, output=b"LLM4AD_STOP_STAGE=wait\n")
        return real(command, **kwargs)

    monkeypatch.setattr(dev, "su", stop_once)
    state = recorder.stop()
    assert state["cleanup_confirmed"] and not state["recovery_unknown"]
    assert len(stops) == 2 and stops[0] == stops[1]
    assert state["stop_errors"] == [{"attempt": 1, "error_type": "TimeoutExpired"}]
    assert sum("perfetto -D" in command for command in dev.commands) == 1


def test_requested_window_duration_is_checked():
    """An interrupted marker closed by cleanup cannot stand for a full trial."""
    report = analysis.analyze(Processor(), MARKER, "fixture.game", seconds=2)
    assert report["metrics"] == {} and report["status"] == "unavailable"


def test_shell_syntax_and_marker_validation(tmp_path):
    """Reject injected marker text and validate the generated startup/stop scripts."""
    dev = Device()
    recorder = capture.Capture(dev, "fixture.game", 2, tmp_path / "capture", "A1")
    recorder.start()
    recorder.stop()
    import shlex

    for command in dev.commands:
        if command.startswith("sh -c "):
            subprocess.run(["sh", "-n"], input=shlex.split(command)[2], text=True, check=True)
    for bad in ("x; touch /tmp/injected", "../marker"):
        with pytest.raises(ValueError):
            capture.marker_script(bad, "B")
    assert f"S|$$|{MARKER}|1" in capture.marker_script(MARKER, "B")
    assert f"F|$$|{MARKER}|1" in capture.marker_script(MARKER, "E")


def test_frames_use_linked_target_presentations_without_surfaceflinger_double_count():
    """Deduplicate display tokens and keep unrelated or ambiguous layers unavailable."""
    rows = [
        {
            "layer_name": "game-layer",
            "display_frame_token": i,
            "present_ts": i * 10_000_000,
            "display_matches": 1,
            "present_type": "On-time Present",
            "jank_type": "None",
        }
        for i in (1, 2, 3)
    ]
    result = analysis.frame_summary(rows + [rows[1]], 0, 40_000_000)
    assert result["linked_presentations"] == 3
    assert result["metrics"]["target_present_interval_p95_ms"] == 10
    assert result["metrics"]["target_present_fps"] == 100
    assert analysis.frame_summary(rows + [rows[0] | {"layer_name": "other"}], 0, 40_000_000)["metrics"] == {}


class Processor:
    """Return controlled SQL results while recording actual query boundaries."""

    def __init__(self, window=True, loss=False):
        """Select complete window and data-loss observations."""
        self.queries, self.window, self.loss = [], window, loss

    def query(self, sql):
        """Provide only data requested by the official processor query interface."""
        self.queries.append(sql)
        if "FROM slice WHERE" in sql:
            rows = [{"ts": 100, "dur": 1_000_000_000}] if self.window else []
        elif "FROM stats" in sql:
            rows = [{"name": "ftrace_cpu_overrun", "severity": "data_loss", "value": 1}] if self.loss else []
        elif "FROM counter c" in sql:
            rows = [{"name": "batt.voltage_uv", "minimum": 4, "maximum": 4, "samples": 5, "unit": None}]
        elif "FROM sched s" in sql:
            rows = [{"running_ns": 1_000_000, "slices": 3}]
        else:
            rows = []
        return [SimpleNamespace(**row) for row in rows]


def test_analysis_uses_marked_window_and_marks_bad_battery_units():
    """Keep voltage 4 invalid rather than manufacturing zero power or rescaling."""
    processor = Processor()
    report = analysis.analyze(processor, MARKER, "fixture.game")
    assert report["battery"]["status"] == "invalid"
    assert report["metrics"] == {"target_cpu_running_ms": 1}
    assert report["frames"]["status"] == "unavailable"
    assert not report["energy_used_for_fitness"]
    assert all("1000000100" in sql for sql in processor.queries if "FROM counter c" in sql or "FROM sched s" in sql)


@pytest.mark.parametrize("window,loss", [(False, False), (True, True)])
def test_incomplete_window_or_trace_loss_cannot_become_valid_metrics(window, loss):
    """A missing marker and lost records retain diagnostics but no accepted metric."""
    report = analysis.analyze(Processor(window=window, loss=loss), MARKER, "fixture.game")
    assert report["status"] == "unavailable" and report["metrics"] == {}


def test_mali_marks_the_actual_existing_window():
    """The marker surrounds native sampling rather than screenshots or warm-up."""
    native = load("mali_capture")._CAPTURE
    assert native.index("echo llm4ad_window_start") < native.index("perfetto_open=1") < native.index('sleep "$seconds"')
    assert native.index('sleep "$seconds"') < native.index("echo llm4ad_window_end")
    assert "charging_enabled" not in native
    assert re.search(r"perfetto_open.*trace_marker", native, re.S)


@pytest.mark.skipif(os.name != "posix", reason="The Android evaluator container uses POSIX process groups")
def test_transport_timeout_releases_grandchild_file_locks(tmp_path, monkeypatch):
    """A hung ADB-like child cannot keep the inherited device lease after timeout."""
    import fcntl

    spec = importlib.util.spec_from_file_location("bounded_transport", ROOT.parent / "gfrc/gfrc_ab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv("PF_LOCK_FDS", raising=False)
    lease = tmp_path / "lease"
    lease.touch()
    ready = tmp_path / "ready"
    program = (
        "import fcntl,os,subprocess,sys,time; from pathlib import Path; "
        f"fd=os.open({str(lease)!r},os.O_RDWR); fcntl.flock(fd,fcntl.LOCK_EX); "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],pass_fds=(fd,)); "
        f"Path({str(ready)!r}).write_text(str(child.pid)); time.sleep(60)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        module.sh(shlex.join([sys.executable, "-c", program]), timeout=1)
    assert ready.exists()
    with lease.open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
