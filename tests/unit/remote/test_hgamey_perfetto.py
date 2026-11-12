"""Verify owned capture recovery and honest window-scoped analysis without a phone."""

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

ROOT = Path(__file__).resolve().parents[3] / "src/llm4ad/gateway/examples/hgamey/protocol/upstream/selfsr"


def load(name):
    """Load adjacent helpers without requiring a device or Perfetto installation."""
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
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

    def su(self, command, timeout=15):
        """Return source content or exact owned recorder receipts."""
        self.commands.append(command)
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
            return "LLM4AD_PERFETTO_REMOVED\n"
        return ""

    def shell(self, command):
        """Retain the original boot identity unless the test requests a reboot."""
        self.commands.append(command)
        if command == "getprop ro.serialno":
            return "fixture-device"
        return "new-boot" if self.reboot else "fixture-boot"

    def adb(self, command, **kwargs):
        """Write only synthetic test evidence to the requested local fixture path."""
        import shlex

        self.commands.append(command)
        Path(shlex.split(command)[-1]).write_bytes(b"test-only-trace")


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
