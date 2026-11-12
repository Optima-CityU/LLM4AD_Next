"""Exercise Mali capture with synthetic evidence and host-only shell fixtures."""

import importlib.util
import json
import os
import re
import shlex
import signal
import subprocess
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[3] / "src/llm4ad/gateway/examples/hgamey/protocol/upstream/selfsr/mali_capture.py"
SPEC = importlib.util.spec_from_file_location("hgamey_mali_capture", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

IDENTITY = "uid=10344\nboot_id=boot-fixture\npid=5412 uid=10344 start=123456"
TRACE = """# entries-in-buffer/entries-written: 7/7   #P:1
 sh-10 [000] ..... 10.000000: tracing_mark_write: llm4ad_window_start
 sh-10 [000] ..... 10.100000: tracing_mark_write: llm4ad_sample_start_0
 sh-10 [000] ..... 10.110000: tracing_mark_write: llm4ad_sample_end_0
 wk-11 [000] ..... 10.500000: gpu_work_period: gpu_id=0 uid=10344 start_time_ns=10100000000 end_time_ns=10400000000 total_active_duration_ns=200000000
 wk-11 [000] ..... 10.600000: GPU_DVFS__Loading: active=60, tiler=4
 sh-10 [000] ..... 11.000000: tracing_mark_write: llm4ad_window_end
 sh-10 [000] ..... 13.001000: tracing_mark_write: llm4ad_flush_end
"""
STATS = "overrun: 0\ncommit overrun: 0\ndropped events: 0"
FRAME = (
    "uid = 10344\nlayerName = SurfaceView[example.game/Main](BLAST)#1\n"
    "totalFrames = 101\ndroppedFrames = 2\npresent2present histogram is as below:\n"
    + " ".join(f"{bucket}ms={ {16: 5, 33: 90, 34: 5}.get(bucket, 0)}"
               for bucket in (*range(35), *range(36, 51, 2), *range(54, 151, 4), *range(200, 1001, 50))) + "\n"
)
SAMPLES = (
    "0\t/sys/kernel/ged/hal/gpu_utilization\t59 0 41\n"
    "0\t/sys/kernel/ged/hal/current_freqency\t53 169000\n"
    "0\t/sys/class/power_supply/battery/temp\t322\n"
    "0\t/sys/class/power_supply/battery/current_now\t229\n"
    "0\t/sys/class/power_supply/battery/voltage_now\t3813\n"
    "0\t/sys/class/power_supply/battery/status\tDischarging\n"
    "0\t/sys/class/power_supply/mtk-battery/current_now\t0\n"
    "0\t/sys/class/power_supply/usb/online\t0\n"
)


class FakeDev:
    """Return fixture output without invoking adb or any other device transport."""

    s = "fixture-serial"

    def __init__(self, *, mali=True, changes=None, error=None):
        """Configure evidence or a transport error for one synthetic window."""
        self.mali = mali
        self.changes = changes or {}
        self.error = error
        self.calls = []

    def su(self, command, timeout):
        """Respond to discovery and capture without executing the supplied shell."""
        self.calls.append((command, timeout))
        if len(self.calls) == 1:
            return f"gpu=Mali-G925\nLLM4AD_MALI_DETECTED={int(self.mali)}\n"
        if self.error:
            raise self.error
        script = shlex.split(command)[2]
        tag = re.search(r"(?m)^tag=(.+)$", script)[1]
        instance = re.search(r"(?m)^T=(.+)$", script)[1]
        sections = {
            "identity_before": IDENTITY, "identity_after": IDENTITY,
            "trace": TRACE, "stats": STATS, "clock": "local [mono_raw] global",
            "samples": SAMPLES, "timestats": FRAME,
            "cleanup": f"instance={instance}\nowned=1\nremoved=1\nsf_disabled=1\nsampler_stopped=1\n"
            "temp_removed=1\nexit_code=0\nstage=complete", "done": "",
        }
        for key, value in self.changes.items():
            if value is None:
                sections.pop(key, None)
            elif callable(value):
                sections[key] = value(sections[key])
            else:
                sections[key] = value
        return "".join(f"\n{tag}:{key}\n{value}\n" for key, value in sections.items())


def test_non_mali_returns_none_without_trace_or_evidence_mutation(tmp_path):
    """Keep unsupported GPUs on the untouched legacy path."""
    dev = FakeDev(mali=False)
    assert module.collect(dev, dev.s, "example.game", 1, tmp_path / "unused") is None
    assert len(dev.calls) == 1
    assert "mkdir" not in dev.calls[0][0]
    assert not (tmp_path / "unused").exists()


def test_capture_merges_parsers_with_explicit_power_profile(tmp_path):
    """Merge independently parsed evidence without inventing game or power metrics."""
    dev = FakeDev()
    result = module.collect(dev, dev.s, "example.game", 1, tmp_path, power_profile="oplus_milli_positive")
    assert result["cleanup_confirmed"] and not result["recovery_unknown"]
    assert result["metrics"]["gpu_uid_active_total_ms"] == 200
    assert result["metrics"]["gpu_uid_active_pct"] == 20
    assert result["metrics"]["gpu_device_utilization_pct"] == 60
    assert result["metrics"]["battery_temperature_c"] == 32.2
    assert result["metrics"]["battery_supply_vi_product_mw"] == pytest.approx(873.177)
    assert not result["provenance"]["power"]["provenance"]["is_whole_device_power"]
    assert result["metrics"]["gpu_device_frequency_mhz"] == 169
    assert result["provenance"]["sysfs"]["provenance"]["frequency_unit"] == "kHz"
    assert result["metrics"]["display_frame_p95_lower_ms"] == 33
    assert not {"game_fps", "gpu_active_ms", "power_mw", "power_watt"} & result["metrics"].keys()
    assert result["timestats"] == FRAME.strip()
    samples = json.loads((tmp_path / "mali-sysfs.json").read_text())
    assert samples[0]["timestamp_ns"] == 10110000000
    assert samples[0]["raw"]["/sys/class/power_supply/battery/current_now"] == "229"
    assert samples[0]["raw"]["/sys/class/power_supply/mtk-battery/current_now"] == "0"
    assert json.loads((tmp_path / "mali-cleanup.json").read_text())["cleanup_confirmed"]


@pytest.mark.parametrize("changes", [
    {"cleanup": None}, {"done": None},
    {"cleanup": lambda s: s.replace("removed=1", "removed=0")},
    {"cleanup": lambda s: s.replace("sampler_stopped=1", "sampler_stopped=0")},
    {"cleanup": lambda s: s.replace("sf_disabled=1", "sf_disabled=0")},
])
def test_unknown_cleanup_never_silently_falls_back(tmp_path, changes):
    """Require a complete and successful receipt before reporting recovery."""
    dev = FakeDev(changes=changes)
    result = module.collect(dev, dev.s, "example.game", 1, tmp_path)
    assert result["recovery_unknown"] and not result["cleanup_confirmed"]
    assert result["metrics"] == {} and result["error"]


@pytest.mark.parametrize("error", [TimeoutError("fixture transport timeout"), KeyboardInterrupt()])
def test_transport_interruption_returns_durable_unknown_status(tmp_path, error):
    """Preserve uncertainty and return control to the owning recovery lifecycle."""
    dev = FakeDev(error=error)
    result = module.collect(dev, dev.s, "example.game", 1, tmp_path)
    assert result["recovery_unknown"] and result["metrics"] == {}
    assert json.loads((tmp_path / "mali-cleanup.json").read_text())["recovery_unknown"]
    assert len(dev.calls) == 2


def test_capability_failure_with_receipt_is_known_clean(tmp_path):
    """Keep unsupported capability reasons distinct from uncertain cleanup."""
    dev = FakeDev(changes={"cleanup": lambda s: s.replace("exit_code=0", "exit_code=1").replace("stage=complete", "stage=capabilities")})
    result = module.collect(dev, dev.s, "example.game", 1, tmp_path)
    assert result["metrics"] == {}
    assert result["cleanup_confirmed"] and not result["recovery_unknown"]
    assert "capabilities" in result["error"]


@pytest.mark.parametrize("after", [
    IDENTITY.replace("uid=10344", "uid=10345"), IDENTITY.replace("start=123456", "start=123457"),
    IDENTITY.replace("boot-fixture", "boot-other"), "",
])
def test_identity_change_rejects_all_metrics_after_cleanup(tmp_path, after):
    """Reject UID changes, PID reuse, device reboot and missing identity."""
    dev = FakeDev(changes={"identity_after": after})
    result = module.collect(dev, dev.s, "example.game", 1, tmp_path)
    assert result["metrics"] == {} and result["error"]
    assert result["cleanup_confirmed"] and not result["recovery_unknown"]


def test_no_uid_gpu_events_does_not_invent_zero(tmp_path):
    """Allow observed display metrics without fabricating unobserved GPU time."""
    dev = FakeDev(changes={"trace": TRACE.replace("uid=10344", "uid=10345")})
    result = module.collect(dev, dev.s, "example.game", 1, tmp_path)
    assert "gpu_uid_active_total_ms" not in result["metrics"]
    assert result["provenance"]["gpu"]["errors"]
    assert result["metrics"]["display_dropped_frames"] == 2


def test_refuses_existing_evidence_without_mutating_it_or_trace(tmp_path):
    """Retain existing recovery evidence unchanged."""
    receipt = tmp_path / "mali-cleanup.json"
    receipt.write_text("previous evidence")
    dev = FakeDev()
    result = module.collect(dev, dev.s, "example.game", 1, tmp_path)
    assert receipt.read_text() == "previous evidence"
    assert result["cleanup_confirmed"] and result["error"]
    assert len(dev.calls) == 1


def test_incomplete_sysfs_marker_pairs_are_not_timed_samples():
    """Never assign an invented timestamp to an interrupted sample."""
    assert module._samples(SAMPLES, TRACE.replace("llm4ad_sample_end_0", "other")) == []


@pytest.mark.parametrize("collision", [False, True])
def test_host_shell_cleans_only_owned_instance_on_capability_failure(tmp_path, collision):
    """Use ordinary temporary directories; no tracefs, adb or device is accessed."""
    instance = tmp_path / "instance"
    if collision:
        instance.mkdir()
        (instance / "foreign").write_text("must survive")
    # Only identity is stubbed. Execute the actual mkdir, capability failure and trap.
    script = module._CAPTURE.replace("section identity_before\nidentity\n", "section identity_before\nprintf 'fixture identity\\n'\n")
    # Simulate tracefs removing its own virtual files when rmdir is called.
    setup = (
        f"T={shlex.quote(str(instance))}\nD={shlex.quote(str(tmp_path / 'data'))}\ntag=FIXTURE\n"
        'rmdir() { if [ -f "$1/foreign" ]; then return 1; fi; '
        'rm -f "$1/tracing_on" "$1/set_event" "$1/buffer_size_kb" "$1/trace_clock"; command rmdir "$1"; }\n'
    )
    run = subprocess.run(["sh", "-c", setup + script], text=True, capture_output=True, timeout=5)
    assert run.returncode != 0
    sections = module._sections(run.stdout, "FIXTURE")
    assert "removed=1" in sections["cleanup"]
    if collision:
        assert (instance / "foreign").read_text() == "must survive"
        assert "owned=0" in sections["cleanup"]
        assert not (instance / "tracing_on").exists()
    else:
        assert not instance.exists()
        assert "owned=1" in sections["cleanup"]


def test_script_has_no_global_ftrace_fallback_or_power_writes():
    """Check shell syntax and forbidden mutation paths without running capture."""
    script = module._CAPTURE
    assert "mkdir -p" not in script and "rm -rf" not in script
    # ATrace markers label the Perfetto window without controlling global tracing.
    assert "/sys/kernel/tracing/trace" not in script.replace("/sys/kernel/tracing/trace_marker", "")
    assert "/proc/uptime" not in script
    assert "charging_enabled" not in script
    assert "sleep 2" in script
    subprocess.run(["sh", "-n"], input=script, text=True, check=True)


@pytest.mark.skipif(os.name != "posix", reason="Requires a POSIX shell and /dev/zero")
def test_identity_rejects_unterminated_process_input_without_hanging(tmp_path):
    """A procfs read without a terminator must not leave a filter running."""
    folder = tmp_path / "1234"
    folder.mkdir()
    (folder / "status").write_text("Uid:\t10344\n")
    (folder / "cmdline").symlink_to("/dev/zero")
    boot = tmp_path / "boot"
    boot.write_text("fixture-boot\n")
    script = "identity() {" + module._CAPTURE.split("identity() {", 1)[1].split("section identity_before", 1)[0]
    script = script.replace("/proc/$pid/", str(tmp_path) + "/$pid/")
    script = script.replace("/proc/sys/kernel/random/boot_id", str(boot))
    setup = 'package=fixture.game\npm() { echo "package:fixture.game uid:10344"; }\npidof() { echo 1234; }\n'
    process = subprocess.Popen(
        ["sh", "-c", setup + script + "\nidentity"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
    )
    try:
        process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
    assert process.returncode == 1
