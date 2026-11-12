"""Collect one leased Mali diagnostic window using an owned trace instance."""

from __future__ import annotations

import importlib.util
import json
import math
import re
import shlex
import uuid
from pathlib import Path

_DETECT = r"""
found=0
for f in /sys/class/misc/mali*/device/gpuinfo /sys/devices/platform/*mali*/gpuinfo /sys/devices/platform/*/*mali*/gpuinfo; do
    if [ -r "$f" ]; then
        value=$(cat "$f") || exit 1
        printf '%s=%s\n' "$f" "$value"
        case "$value" in *Mali*|*mali*) found=1;; esac
    fi
done
printf 'LLM4AD_MALI_DETECTED=%s\n' "$found"
"""

# Samples use marker timestamps, not uptime or a host clock. No power node is written.
_CAPTURE = r"""
set -eu
owned=0; temporary=0; sf_owned=0; sampler=''; stage=identity_before; perfetto_open=0
section() { printf '\n%s:%s\n' "$tag" "$1"; }
cleanup() {
    rc=$?
    trap - EXIT INT TERM HUP
    set +e
    stopped=1; removed=1; sf_disabled=1; temp_removed=1
    if [ "$perfetto_open" = 1 ]; then
        printf 'F|%s|%s|1' "$$" "$perfetto_marker" > /sys/kernel/tracing/trace_marker
    fi
    if [ -n "$sampler" ]; then
        kill "$sampler" 2>/dev/null
        wait "$sampler" 2>/dev/null
        if kill -0 "$sampler" 2>/dev/null; then stopped=0; fi
    fi
    if [ "$sf_owned" = 1 ]; then
        dumpsys SurfaceFlinger --timestats -disable >/dev/null 2>&1 || sf_disabled=0
    fi
    if [ "$owned" = 1 ]; then
        echo 0 > "$T/tracing_on"
        echo > "$T/set_event"
        rmdir "$T"
        if [ -e "$T" ]; then removed=0; fi
    fi
    if [ "$temporary" = 1 ]; then
        rm -f "$D/samples" "$D/timestats"
        rmdir "$D"
        if [ -e "$D" ]; then temp_removed=0; fi
    fi
    section cleanup
    printf 'instance=%s\nowned=%s\nremoved=%s\nsf_disabled=%s\nsampler_stopped=%s\ntemp_removed=%s\nexit_code=%s\nstage=%s\n' \
        "$T" "$owned" "$removed" "$sf_disabled" "$stopped" "$temp_removed" "$rc" "$stage"
    section done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
identity() {
    uid=$(pm list packages -U "$package" | awk -v p="package:$package" '$1 == p {sub(/^uid:/,"",$2); print $2}')
    case "$uid" in ''|*[!0-9]*) return 1;; esac
    printf 'uid=%s\nboot_id=' "$uid"
    cat /proc/sys/kernel/random/boot_id || return 1
    owners=$(pm list packages -U --uid "$uid") || return 1
    [ "$owners" = "package:$package uid:$uid" ] || return 1
    pids=$(pidof "$package") || return 1
    for pid in $pids; do
        process_uid=$(awk '/^Uid:/ {print $2}' "/proc/$pid/status") || return 1
        [ "$process_uid" = "$uid" ] || return 1
        # Bound procfs input before filtering, including unterminated command lines.
        command=$(head -c 8192 "/proc/$pid/cmdline" | tr '\000' '\n' | head -n 1)
        [ "$command" = "$package" ] || return 1
        start=$(sed 's/^.*) //' "/proc/$pid/stat" | awk '{print $20}')
        case "$start" in ''|*[!0-9]*) return 1;; esac
        printf 'pid=%s uid=%s start=%s\n' "$pid" "$process_uid" "$start"
    done
}
section identity_before
identity
stage=private_instance
mkdir "$T"
owned=1
echo 0 > "$T/tracing_on"
stage=capabilities
echo 4096 > "$T/buffer_size_kb"
echo mono_raw > "$T/trace_clock"
section clock
cat "$T/trace_clock"
case "$(cat "$T/trace_clock")" in *'[mono_raw]'*|mono_raw) ;; *) exit 1;; esac
section events
for event in power/gpu_work_period ged/GPU_DVFS__Loading; do
    echo 1 > "$T/events/$event/enable"
    printf 'required=%s\n' "$event"
done
for event in power/gpu_frequency ged/tracing_mark_write ged/GPU_DVFS__Policy__Frame_based__GPU_Time; do
    if [ -e "$T/events/$event/enable" ]; then
        echo 1 > "$T/events/$event/enable"
        printf 'optional=%s\n' "$event"
    fi
done
stage=temporary_directory
mkdir "$D"
temporary=1
sample() {
    trap - EXIT INT TERM HUP
    i=0
    while [ "$i" -lt "$sample_limit" ]; do
        echo "llm4ad_sample_start_$i" > "$T/trace_marker"
        for f in /sys/kernel/ged/hal/gpu_utilization /sys/kernel/ged/hal/current_freqency \
            /sys/class/power_supply/battery/status /sys/class/power_supply/battery/current_now \
            /sys/class/power_supply/battery/voltage_now /sys/class/power_supply/battery/temp \
            /sys/class/power_supply/mtk-battery/status /sys/class/power_supply/mtk-battery/current_now \
            /sys/class/power_supply/mtk-battery/voltage_now /sys/class/power_supply/mtk-battery/temp \
            /sys/class/power_supply/usb/online /sys/class/power_supply/usb/current_now; do
            if [ -r "$f" ] && value=$(cat "$f"); then
                printf '%s\t%s\t%s\n' "$i" "$f" "$value"
            fi
        done
        echo "llm4ad_sample_end_$i" > "$T/trace_marker"
        i=$((i + 1))
        sleep 1
    done
}
stage=window
sf_owned=1
dumpsys SurfaceFlinger --timestats -disable -clear >/dev/null
echo 1 > "$T/tracing_on"
dumpsys SurfaceFlinger --timestats -enable >/dev/null
echo llm4ad_window_start > "$T/trace_marker"
if [ -n "$perfetto_marker" ]; then
    if printf 'S|%s|%s|1' "$$" "$perfetto_marker" > /sys/kernel/tracing/trace_marker; then
        perfetto_open=1
    fi
fi
sample > "$D/samples" &
sampler=$!
sleep "$seconds"
echo llm4ad_window_end > "$T/trace_marker"
if [ "$perfetto_open" = 1 ]; then
    printf 'F|%s|%s|1' "$$" "$perfetto_marker" > /sys/kernel/tracing/trace_marker
    perfetto_open=0
fi
dumpsys SurfaceFlinger --timestats -disable >/dev/null
# Keep tracing for delayed UID work events; SF statistics remain frozen.
sleep 2
kill "$sampler" 2>/dev/null || true
wait "$sampler" 2>/dev/null || true
if kill -0 "$sampler" 2>/dev/null; then exit 1; fi
sampler=''
echo llm4ad_flush_end > "$T/trace_marker"
echo 0 > "$T/tracing_on"
stage=readout
dumpsys SurfaceFlinger --timestats -dump -maxlayers 128 > "$D/timestats"
section stats
for f in "$T"/per_cpu/cpu*/stats; do printf 'STATS=%s\n' "$f"; cat "$f"; done
section trace
cat "$T/trace"
section samples
cat "$D/samples"
section timestats
cat "$D/timestats"
section identity_after
identity
stage=complete
"""


def _parser(name: str):
    """Load the adjacent pure parser without changing the caller's import path."""
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sections(text: str, tag: str) -> dict[str, str]:
    """Decode uniquely tagged sections, including the completed cleanup receipt."""
    parts = re.split(rf"(?m)^{re.escape(tag)}:([a-z_]+)\r?$", text)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


def _identity(text: str) -> dict:
    """Require an exact UID, live process start identity and device boot identity."""
    uid = re.search(r"(?m)^uid=(\d+)$", text)
    boot = re.search(r"(?m)^boot_id=([a-zA-Z0-9-]+)$", text)
    pids = re.findall(r"(?m)^pid=(\d+) uid=(\d+) start=(\d+)$", text)
    if not uid or not boot or not pids or any(p[1] != uid[1] for p in pids):
        raise ValueError("Package UID/process identity is unavailable or inconsistent")
    return {"uid": int(uid[1]), "boot_id": boot[1], "processes": sorted(pids)}


def _samples(text: str, trace: str) -> list[dict]:
    """Attach raw reads to complete sample marker pairs on CLOCK_MONOTONIC_RAW."""
    markers = {}
    for seconds, fraction, edge, index in re.findall(
        r"\b(\d+)\.(\d{1,9}):\s+(?:tracing_mark_write|trace_marker):\s+llm4ad_sample_(start|end)_(\d+)\s*$",
        trace, re.MULTILINE,
    ):
        key = (index, edge)
        if key in markers:
            raise ValueError("Repeated sysfs sample marker")
        markers[key] = int(seconds) * 1_000_000_000 + int(fraction.ljust(9, "0"))
    rows = {}
    for line in text.splitlines():
        fields = line.split("\t", 2)
        if len(fields) == 3:
            index, path, value = fields
            rows.setdefault(index, {})[path] = value
    samples = []
    for index, raw in rows.items():
        start, end = markers.get((index, "start")), markers.get((index, "end"))
        if start is None or end is None or start > end:
            continue
        sample = {"timestamp_ns": end, "read_start_ns": start, "raw": raw}
        for key, path in {
            "gpu_utilization": "/sys/kernel/ged/hal/gpu_utilization",
            "current_freqency": "/sys/kernel/ged/hal/current_freqency",
            "battery_temp": "/sys/class/power_supply/battery/temp",
        }.items():
            if path in raw:
                sample[key] = raw[path]
        samples.append(sample)
    return sorted(samples, key=lambda row: row["timestamp_ns"])


def collect(dev, serial, package, seconds, output: Path, power_profile="disabled", perfetto_marker=None) -> dict | None:
    """Collect trace, presentation statistics and raw sysfs in one synchronous window.

    Args:
        dev: Existing leased upstream Dev instance; only its su method is used.
        serial: Expected Dev transport serial, checked against dev.s.
        package: Exact Android package whose UID and processes are verified twice.
        seconds: Finite sampling window in seconds, between 1 and 120.
        output: Per-window evidence directory. Existing capture evidence is refused.
        power_profile: Explicit disabled, linux_micro_negative or oplus_milli_positive.
            Units and discharge polarity are explicit; no whole-device scale is inferred.
        perfetto_marker: Optional owned ATrace label marking this exact native window.

    Returns:
        None only for a completed non-Mali detection before trace mutation. Otherwise
        metrics, provenance, raw timestats, cleanup_confirmed and recovery_unknown.
        Capture errors, including transport interruptions, return an error dictionary.
        Callers must stop further device work when recovery_unknown is true.
    """
    result = {
        "metrics": {}, "provenance": {}, "timestats": "",
        "cleanup_confirmed": True, "recovery_unknown": False,
    }
    submitted = False
    raw = ""
    folder = None
    try:
        if getattr(dev, "s", None) != serial or not serial:
            raise ValueError("Dev serial does not match requested serial")
        if not isinstance(package, str) or not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", package):
            raise ValueError("Invalid Android package")
        if isinstance(seconds, bool) or not math.isfinite(float(seconds)) or not 1 <= float(seconds) <= 120:
            raise ValueError("seconds must be between 1 and 120")
        if power_profile not in {"disabled", "linux_micro_negative", "oplus_milli_positive"}:
            raise ValueError("Unknown power_profile")
        if perfetto_marker is not None and not re.fullmatch(r"llm4ad_window_[a-f0-9]{32}", perfetto_marker):
            raise ValueError("Invalid Perfetto window marker")
        detection = dev.su("sh -c " + shlex.quote(_DETECT), timeout=15)
        if detection.rstrip().endswith("LLM4AD_MALI_DETECTED=0"):
            return None
        if not detection.rstrip().endswith("LLM4AD_MALI_DETECTED=1"):
            raise ValueError("Mali detection did not complete")
        token = uuid.uuid4().hex
        tag = "LLM4AD_MALI_" + token
        instance = "/sys/kernel/tracing/instances/llm4ad_mali_" + token
        temporary = "/data/local/tmp/llm4ad_mali_" + token
        provenance = result["provenance"]
        provenance.update(
            source="owned_private_ftrace", serial=serial, package=package, detection=detection,
            instance=instance, temporary_directory=temporary, trace_clock="mono_raw",
            requested_seconds=float(seconds), tail_seconds=2, power_profile=power_profile,
            sysfs_timestamp="private mono_raw sample end marker; read_start_ns bounds each read batch",
            sf_window="enable before start marker; disable after end marker; command latency brackets the trace window",
            sysfs_frequency_unit="kHz",
            sysfs_frequency_unit_source=(
                "https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_mt6991/blob/"
                "76d6e1e5b8deb0bcd00355d7a9e68638a501d8bc/"
                "kernel/kernel_device_modules-6.6/drivers/gpu/mediatek/ged/src/ged_hal.c#L220"
            ),
        )
        folder = Path(output)
        folder.mkdir(parents=True, exist_ok=True)
        evidence_names = (
            "mali-cleanup.json", "mali-capture.sh", "mali-capture.txt",
            "mali-trace.txt", "surface-timestats.txt", "mali-sysfs.json",
        )
        if any((folder / name).exists() for name in evidence_names):
            raise ValueError("Capture evidence already exists; use a fresh window directory")
        # Reserve a durable intent before any remote mutation. Never overwrite evidence.
        with (folder / "mali-cleanup.json").open("x") as handle:
            json.dump({"instance": instance, "temporary_directory": temporary, "submitted": False}, handle)
        script = "\n".join(
            f"{key}={shlex.quote(str(value))}" for key, value in {
                "T": instance, "D": temporary, "tag": tag, "package": package,
                "seconds": float(seconds), "sample_limit": math.ceil(float(seconds)) + 3,
                "perfetto_marker": perfetto_marker or "",
            }.items()
        ) + "\n" + _CAPTURE
        (folder / "mali-capture.sh").write_text(script)
        result.update(cleanup_confirmed=False, recovery_unknown=True)
        (folder / "mali-cleanup.json").write_text(json.dumps(result, indent=2))
        submitted = True
        try:
            raw = dev.su("sh -c " + shlex.quote(script), timeout=float(seconds) + 60)
        except (Exception, KeyboardInterrupt) as error:
            partial = getattr(error, "stdout", None) or getattr(error, "output", "") or ""
            raw = partial.decode(errors="replace") if isinstance(partial, bytes) else partial
            result["error"] = f"{type(error).__name__}: {error}"
        (folder / "mali-capture.txt").write_text(raw)
        parts = _sections(raw, tag)
        receipt = dict(re.findall(r"(?m)^([a-z_]+)=(.*)$", parts.get("cleanup", "")))
        confirmed = (
            "done" in parts and receipt.get("instance") == instance
            and all(receipt.get(k) == "1" for k in ("removed", "sf_disabled", "sampler_stopped", "temp_removed"))
        )
        provenance["cleanup_receipt"] = receipt
        result.update(cleanup_confirmed=confirmed, recovery_unknown=not confirmed)
        if not confirmed:
            raise ValueError("Owned capture cleanup receipt is missing or unsuccessful")
        if result.get("error"):
            return result
        if receipt.get("exit_code") != "0" or receipt.get("stage") != "complete":
            raise ValueError("Mali capture unavailable at stage " + receipt.get("stage", "unknown"))
        before, after = _identity(parts.get("identity_before", "")), _identity(parts.get("identity_after", ""))
        provenance.update(identity_before=before, identity_after=after)
        if before != after:
            raise ValueError("Package UID/process/device identity changed during capture")
        trace, stats = parts.get("trace", ""), parts.get("stats", "")
        result["timestats"] = parts.get("timestats", "")
        (folder / "mali-trace.txt").write_text(stats + "\n" + trace)
        (folder / "surface-timestats.txt").write_text(result["timestats"])
        samples = _samples(parts.get("samples", ""), trace)
        (folder / "mali-sysfs.json").write_text(json.dumps(samples, indent=2))
        parser = _parser("mali_telemetry")
        gpu_ids = set(re.findall(r"gpu_work_period:.*?\bgpu_id=(\d+)\s+uid=" + str(before["uid"]) + r"\b", trace))
        if len(gpu_ids) != 1:
            gpu = {"metrics": {}, "errors": ["No unique GPU id observed for the verified application UID"]}
        else:
            gpu = parser.parse_ftrace(trace, uid=before["uid"], gpu_id=int(next(iter(gpu_ids))),
                                      trace_clock=parts.get("clock", ""), stats_text=stats)
        provenance["gpu"] = gpu
        result["metrics"].update(gpu["metrics"])
        coverage = gpu.get("coverage", {})
        if "window_start_ns" in coverage and "window_end_ns" in coverage:
            start, end = coverage["window_start_ns"], coverage["window_end_ns"]
            valid_samples = [s for s in samples if start <= s["read_start_ns"] <= s["timestamp_ns"] < end]
            sysfs = parser.parse_sysfs_samples(
                valid_samples, window_start_ns=start, window_end_ns=end, frequency_unit="kHz",
            )
            provenance["sysfs"] = sysfs
            for key, value in sysfs["metrics"].items():
                result["metrics"].setdefault(key, value)
            power = _parser("power_telemetry").parse_power_samples(
                samples, window_start_ns=start, window_end_ns=end, profile=power_profile,
            )
            provenance["power"] = power
            result["metrics"].update(power["metrics"])
        frame = _parser("frame_telemetry").parse_frame_stats(result["timestats"], package, before["uid"])
        provenance["frame"] = frame
        result["metrics"].update(frame["metrics"])
    except (Exception, KeyboardInterrupt) as error:
        result["metrics"] = {}
        result["error"] = f"{type(error).__name__}: {error}"
        if not submitted:
            result.update(cleanup_confirmed=True, recovery_unknown=False)
    finally:
        if folder is not None and submitted:
            try:
                (folder / "mali-cleanup.json").write_text(json.dumps(result, indent=2))
            except OSError as error:
                result["error"] = f"Could not persist cleanup receipt: {error}"
    return result
