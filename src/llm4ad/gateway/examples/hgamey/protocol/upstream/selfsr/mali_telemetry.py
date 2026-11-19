"""Parse caller-collected Mali telemetry without device or filesystem access.

Collector contract:
    Use a private ftrace instance with trace_clock=mono_raw, one pair of literal
    llm4ad_window_start/end markers, and at least two seconds of post-window
    tracing for delayed GPU work events. Write llm4ad_flush_end before disabling
    tracing to prove that interval. Supply raw per-CPU stats in stats_text or
    prepend them to text. Trace timestamps define the half-open window.
    parse_ftrace returns that window in coverage for parse_sysfs_samples.

    Sysfs samples are dictionaries containing an integer timestamp_ns from the
    same CLOCK_MONOTONIC_RAW clock and optional raw strings: gpu_utilization
    (GED's active, blocked, idle percentages), current_freqency (GED's index and
    frequency), and battery_temp (battery/temp in tenths of a degree Celsius).
    Missing nodes are omitted or None. Callers own capture and restoration.

Both public functions return status, metrics, coverage, provenance, errors and
warnings. Status is invalid for rejected evidence (empty metrics), unsupported
when no supported metric is available, partial when valid metrics have explicit
limitations, and ok otherwise. These are telemetry statuses, never fitness or
game improvement verdicts. Frequencies require a caller-confirmed unit; None
does not guess one. Means of device samples are arithmetic, not time-weighted.

GPU periods describe UID/GPU work, never frames. The driver supplies active time
already accounting for concurrent work; this parser cannot independently verify
hardware accounting. The field contract is documented by AOSP gpuWork.c:
https://android.googlesource.com/platform/frameworks/native/+/refs/heads/main/services/gpuservice/gpuwork/bpfprogs/gpuWork.c
"""

from __future__ import annotations

import hashlib
import json
import math
import re

_NS = 1_000_000_000
_MAX_U64 = (1 << 64) - 1
_EVENT = re.compile(r"\b(\d+)\.(\d{1,9}):\s+([\w/]+(?::\w+)?):\s*(.*)$")
_FIELDS = re.compile(r"\b(\w+)=([^\s,]+)")
_LOSS = re.compile(r"^\s*(overrun|commit overrun|dropped events):\s*(\S+)\s*$", re.MULTILINE)
_FREQUENCY_FACTORS = {"Hz": 1e-6, "kHz": 1e-3, "MHz": 1.0}


def _result(source: str) -> dict:
    """Create an unscored telemetry result."""
    return {
        "status": "unsupported",
        "metrics": {},
        "coverage": {},
        "provenance": {"schema": "llm4ad/mali-telemetry@1", "source": source, "metric_sources": {}},
        "errors": [],
        "warnings": [],
    }


def _finish(result: dict) -> dict:
    """Keep invalid evidence out of numeric telemetry."""
    if result["errors"]:
        result["metrics"] = {}
        result["status"] = "invalid"
    elif result["metrics"]:
        result["status"] = "partial" if result["warnings"] else "ok"
    return result


def _integer(value: object, name: str, maximum: int = _MAX_U64) -> int:
    """Read an unsigned integer without accepting booleans or rounding."""
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        value = int(value)
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"Invalid {name}: expected an unsigned integer")
    return value


def _window(result: dict, start: int, end: int) -> None:
    """Retain the exact marker-derived interval used as the denominator."""
    if start >= end:
        raise ValueError("Window end must be after window start")
    result["coverage"].update(window_start_ns=start, window_end_ns=end, window_duration_ns=end - start)


def _mean(result: dict, name: str, values: list[float], source: str, unit: str) -> None:
    """Expose a device sample mean with its scope and sample count."""
    result["coverage"][f"{name}_samples"] = len(values)
    if values:
        result["metrics"][name] = math.fsum(value / len(values) for value in values)
        result["provenance"]["metric_sources"][name] = {
            "source": source,
            "scope": "device",
            "aggregation": "arithmetic_sample_mean",
            "unit": unit,
        }


def _loss_stats(text: str, stats: str, result: dict) -> None:
    """Reject buffer loss and incomplete supplied per-CPU loss counters."""
    counts = {name: [] for name in ("overrun", "commit overrun", "dropped events")}
    for name, value in _LOSS.findall(stats):
        counts[name].append(_integer(value, name))
    lengths = {len(values) for values in counts.values()}
    if 0 in lengths or len(lengths) != 1:
        raise ValueError("Missing or incomplete per-CPU overrun, commit overrun and dropped events stats")
    cpus = re.search(r"#P:(\d+)", text)
    if cpus and len(counts["overrun"]) != int(cpus[1]):
        raise ValueError("Loss stats do not cover every CPU declared by the trace header")
    result["provenance"]["loss_counters"] = counts
    if any(value for values in counts.values() for value in values):
        raise ValueError("Trace buffer overrun or dropped events: telemetry is incomplete")
    if re.search(r"\bLOST\s+(?:[1-9][0-9]*\s+)?EVENTS\b", text, re.IGNORECASE):
        raise ValueError("Trace reports lost events")
    entries = re.search(r"entries-in-buffer/entries-written:\s*(\d+)/(\d+)", text)
    if entries and int(entries[1]) != int(entries[2]):
        raise ValueError("Trace entries-in-buffer differs from entries-written")


def parse_ftrace(
    text: str,
    *,
    uid: int,
    gpu_id: int,
    trace_clock: str,
    stats_text: str | None = None,
    gpu_freq_unit: str | None = None,
) -> dict:
    """Parse one private marker window with exact UID and GPU filtering.

    Args:
        text: Raw textual ftrace output, optionally prefixed by per-CPU stats.
        uid: Android application UID, not PID or a substring of another UID.
        gpu_id: Exact power/gpu_work_period GPU identifier.
        trace_clock: Confirmed private-instance clock, mono_raw or a clock menu
            with [mono_raw] selected. Other clocks are rejected.
        stats_text: Raw stats for every traced CPU; defaults to searching text.
        gpu_freq_unit: Confirmed GED gpu_freq counter unit: MHz, kHz, Hz or None.

    Returns:
        Telemetry with active_pct = total included active / entire window * 100.
        Boundary periods are discarded whole, never proportionally clipped.
        Coverage records discarded overlap and included period duration. Gaps
        may represent idle GPU time; coverage is not a frame count. Exactly one
        llm4ad_flush_end marker at least two seconds after window end confirms
        flushing. Missing flush evidence leaves metrics partial; duplicate or
        short flush evidence is invalid.
    """
    result = _result("private_ftrace")
    result["coverage"]["post_window_flush_confirmed"] = False
    try:
        uid = _integer(uid, "uid", (1 << 32) - 1)
        gpu_id = _integer(gpu_id, "gpu_id", (1 << 32) - 1)
        selected_clock = re.findall(r"\[([a-z_]+)\]", trace_clock)
        clock = selected_clock[0] if len(selected_clock) == 1 else trace_clock.strip()
        if clock != "mono_raw":
            raise ValueError("trace_clock must be mono_raw to match GPU payload CLOCK_MONOTONIC_RAW")
        for readback in re.findall(r"^\s*#?\s*(?:TRACE_CLOCK|trace_clock)\s*[:=]\s*(.+)$", text, re.MULTILINE):
            selected = re.findall(r"\[([a-z_]+)\]", readback)
            observed = selected[0] if len(selected) == 1 else readback.strip()
            if observed != clock:
                raise ValueError("Embedded trace_clock readback contradicts the requested mono_raw clock")
        if gpu_freq_unit is not None and gpu_freq_unit not in _FREQUENCY_FACTORS:
            raise ValueError("Unsupported gpu_freq_unit; use confirmed Hz, kHz or MHz")
        result["provenance"].update(
            trace_clock=clock,
            uid=uid,
            gpu_id=gpu_id,
            trace_sha256=hashlib.sha256(text.encode()).hexdigest(),
            stats_sha256=hashlib.sha256((text if stats_text is None else stats_text).encode()).hexdigest(),
            gpu_freq_unit=gpu_freq_unit,
            clock_validation="caller-confirmed mono_raw; payload end <= event time within print precision; delay <= 2 seconds",
            post_window_flush="one llm4ad_flush_end marker at least 2 seconds after window end on the trace clock",
        )
        _loss_stats(text, text if stats_text is None else stats_text, result)
        events, markers, flush_markers = [], [], []
        previous_time = -1
        for line in text.splitlines():
            match = _EVENT.search(line)
            if not match:
                if any(token in line for token in ("gpu_work_period:", "llm4ad_window_", "llm4ad_flush_", "GPU_DVFS__Loading:")):
                    raise ValueError("Malformed telemetry event or marker timestamp")
                continue
            seconds, fraction, name, body = match.groups()
            timestamp = int(seconds) * _NS + int(fraction.ljust(9, "0"))
            precision = 10 ** (9 - len(fraction))
            if timestamp < previous_time:
                raise ValueError("Trace event timestamps are not monotonic; check the private trace clock")
            previous_time = timestamp
            name = name.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
            events.append((timestamp, precision, name, body))
            if name in {"tracing_mark_write", "trace_marker"} and body.startswith("llm4ad_window_"):
                markers.append((body.strip(), timestamp))
            if name in {"tracing_mark_write", "trace_marker"} and body.startswith("llm4ad_flush_"):
                if body.strip() != "llm4ad_flush_end":
                    raise ValueError("Malformed llm4ad_flush_end marker")
                flush_markers.append(timestamp)
        if len(markers) != 2 or [item[0] for item in markers] != ["llm4ad_window_start", "llm4ad_window_end"]:
            raise ValueError("Require exactly one ordered llm4ad_window_start/end marker pair")
        start, end = markers[0][1], markers[1][1]
        _window(result, start, end)
        if len(flush_markers) > 1:
            raise ValueError("Require exactly one llm4ad_flush_end marker; duplicate flush markers")
        if flush_markers:
            flush_duration = flush_markers[0] - end
            result["coverage"].update(
                flush_end_ns=flush_markers[0], post_window_flush_duration_ns=flush_duration
            )
            if flush_duration < 2 * _NS:
                raise ValueError("llm4ad_flush_end must be at least 2 seconds after window end")
            result["coverage"]["post_window_flush_confirmed"] = True
        else:
            result["warnings"].append("Missing llm4ad_flush_end marker; post-window flush completeness is unconfirmed")
        work = {
            "matched_periods": 0,
            "included_periods": 0,
            "outside_periods": 0,
            "boundary_discarded_periods": 0,
            "boundary_discarded_overlap_ns": 0,
            "included_duration_ns": 0,
            "total_active_ns": 0,
        }
        result["coverage"]["gpu_work_period"] = work
        previous_start, previous_end = -1, -1
        utilization, frequencies = [], []
        for timestamp, precision, name, body in events:
            if name == "gpu_work_period":
                pairs = _FIELDS.findall(body)
                fields = dict(pairs)
                if len(pairs) != len(fields):
                    raise ValueError("Duplicate fields in GPU work period")
                if _integer(fields.get("uid"), "period uid", (1 << 32) - 1) != uid:
                    continue
                if _integer(fields.get("gpu_id"), "period gpu_id", (1 << 32) - 1) != gpu_id:
                    continue
                begin = _integer(fields.get("start_time_ns"), "period start_time_ns")
                finish = _integer(fields.get("end_time_ns"), "period end_time_ns")
                active = _integer(fields.get("total_active_duration_ns"), "period total_active_duration_ns")
                duration = finish - begin
                if not 0 < duration <= _NS or active > duration:
                    raise ValueError("Invalid GPU period duration or active time exceeds duration")
                if begin <= previous_start or begin < previous_end:
                    raise ValueError("Selected UID/GPU periods overlap, repeat or are not strictly increasing")
                if finish > timestamp + precision or timestamp - finish > 2 * _NS + precision:
                    raise ValueError("GPU payload and trace clock are inconsistent or event emission exceeds 2 seconds")
                previous_start, previous_end = begin, finish
                work["matched_periods"] += 1
                if finish <= start or begin >= end:
                    work["outside_periods"] += 1
                elif begin < start or finish > end:
                    work["boundary_discarded_periods"] += 1
                    work["boundary_discarded_overlap_ns"] += min(finish, end) - max(begin, start)
                else:
                    work["included_periods"] += 1
                    work["included_duration_ns"] += duration
                    work["total_active_ns"] += active
            elif start <= timestamp < end and name == "GPU_DVFS__Loading":
                fields = _FIELDS.findall(body)
                values = [value for key, value in fields if key == "active"]
                if len(values) != 1:
                    raise ValueError("GPU_DVFS__Loading requires exactly one active percentage")
                utilization.append(float(_integer(values[0], "device active percentage", 100)))
            elif start <= timestamp < end and name == "tracing_mark_write" and re.match(r"C\|[^|]+\|gpu_freq\|", body):
                value = _integer(body.rsplit("|", 1)[-1], "gpu_freq counter")
                frequencies.append(value)
        duration = end - start
        work.update(
            period_coverage_pct=100 * work["included_duration_ns"] / duration,
            uncovered_window_ns=duration - work["included_duration_ns"],
            active_pct_denominator_ns=duration,
            boundary_policy="discard whole periods; no active-time interpolation",
        )
        if work["boundary_discarded_periods"]:
            result["warnings"].append("Boundary periods discarded; UID active total and percentage are lower bounds")
        if work["included_periods"]:
            result["metrics"].update(
                gpu_uid_active_total_ms=work["total_active_ns"] / 1_000_000,
                gpu_uid_active_pct=100 * work["total_active_ns"] / duration,
            )
            for metric, unit in (("gpu_uid_active_total_ms", "ms"), ("gpu_uid_active_pct", "% of full window")):
                result["provenance"]["metric_sources"][metric] = {
                    "source": "power/gpu_work_period",
                    "scope": "uid_gpu",
                    "uid": uid,
                    "gpu_id": gpu_id,
                    "unit": unit,
                    "aggregation": "sum of driver-reported active time from fully contained periods",
                }
        else:
            result["warnings"].append("No fully contained periods for the exact UID/GPU; UID active metrics unavailable")
        _mean(result, "gpu_device_utilization_pct", utilization, "ged/GPU_DVFS__Loading.active", "%")
        if gpu_freq_unit is None and frequencies:
            result["coverage"]["unconverted_gpu_freq_samples"] = len(frequencies)
            result["warnings"].append("gpu_freq unit unconfirmed; frequency metric omitted")
        else:
            values = [value * _FREQUENCY_FACTORS[gpu_freq_unit] for value in frequencies]
            _mean(result, "gpu_device_frequency_mhz", values, "ged/tracing_mark_write gpu_freq", "MHz")
    except (ValueError, TypeError, AttributeError, OverflowError) as error:
        result["errors"].append(str(error))
    return _finish(result)


def parse_sysfs_samples(
    samples: list[dict],
    *,
    window_start_ns: int,
    window_end_ns: int,
    frequency_unit: str | None = None,
) -> dict:
    """Summarize already captured sysfs samples in the marker window.

    Args:
        samples: Strictly increasing timestamp_ns records on CLOCK_MONOTONIC_RAW.
            Optional raw fields are gpu_utilization, current_freqency and
            battery_temp; see the module collector contract. No nodes are read.
        window_start_ns: Start marker timestamp from parse_ftrace.coverage.
        window_end_ns: End marker timestamp, excluded from this window.
        frequency_unit: Confirmed current_freqency second-column unit, or None.

    Returns:
        Device-scoped arithmetic means, coverage counts and source provenance.
        Missing measurements remain absent and are never replaced with zeros.
    """
    result = _result("sysfs_samples")
    try:
        start = _integer(window_start_ns, "window_start_ns")
        end = _integer(window_end_ns, "window_end_ns")
        _window(result, start, end)
        if frequency_unit is not None and frequency_unit not in _FREQUENCY_FACTORS:
            raise ValueError("Unsupported frequency_unit; use confirmed Hz, kHz or MHz")
        result["provenance"].update(
            trace_clock="mono_raw",
            frequency_unit=frequency_unit,
            clock_validation="caller supplies timestamps on the marker CLOCK_MONOTONIC_RAW clock",
            samples_sha256=hashlib.sha256(json.dumps(samples, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        )
        utilization, frequencies, temperatures = [], [], []
        previous = -1
        included = 0
        for sample in samples:
            timestamp = _integer(sample.get("timestamp_ns"), "sample timestamp_ns")
            if timestamp <= previous:
                raise ValueError("Sysfs timestamps must be strictly increasing without duplicates")
            previous = timestamp
            if not start <= timestamp < end:
                continue
            included += 1
            if sample.get("gpu_utilization") is not None:
                fields = str(sample["gpu_utilization"]).split()
                if len(fields) != 3:
                    raise ValueError("gpu_utilization requires active, blocked and idle percentage columns")
                values = [_integer(value, "gpu_utilization percentage", 100) for value in fields]
                utilization.append(float(values[0]))
            if sample.get("current_freqency") is not None:
                fields = str(sample["current_freqency"]).split()
                if len(fields) != 2:
                    raise ValueError("current_freqency requires an index and frequency")
                _integer(fields[0], "frequency index")
                frequencies.append(_integer(fields[1], "frequency"))
            if sample.get("battery_temp") is not None:
                value = str(sample["battery_temp"]).strip()
                if not re.fullmatch(r"-?[0-9]+", value):
                    raise ValueError("battery_temp must be an integer in tenths of a degree Celsius")
                temperature = int(value) / 10
                if not -100 <= temperature <= 200:
                    raise ValueError("battery_temp is outside the supported Celsius range")
                temperatures.append(temperature)
        result["coverage"].update(total_samples=len(samples), included_samples=included, outside_samples=len(samples) - included)
        _mean(result, "gpu_device_utilization_pct", utilization, "/sys/kernel/ged/hal/gpu_utilization active column", "%")
        _mean(result, "battery_temperature_c", temperatures, "/sys/class/power_supply/battery/temp", "C")
        if frequency_unit is None and frequencies:
            result["coverage"]["unconverted_frequency_samples"] = len(frequencies)
            result["warnings"].append("current_freqency unit unconfirmed; frequency metric omitted")
        else:
            values = [value * _FREQUENCY_FACTORS[frequency_unit] for value in frequencies]
            _mean(result, "gpu_device_frequency_mhz", values, "/sys/kernel/ged/hal/current_freqency second column", "MHz")
    except (ValueError, TypeError, AttributeError, OverflowError) as error:
        result["errors"].append(str(error))
    return _finish(result)
