"""Synthetic Mali parser fixtures only; no device reads or private trace copies."""

import importlib.util
import json
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[3] / "src/llm4ad/gateway/examples/hgamey/protocol/upstream/selfsr/mali_telemetry.py"
SPEC = importlib.util.spec_from_file_location("synthetic_mali_telemetry_test", MODULE)
telemetry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(telemetry)
pytestmark = pytest.mark.unit

NS = 1_000_000_000
START, END = 10 * NS, 11 * NS
STATS = "overrun: 0\ncommit overrun: 0\ndropped events: 0\n"


def event(timestamp, name, body):
    """Render an invented nanosecond ftrace event with no hardware evidence."""
    return f"synthetic-91 [000] ..... {timestamp // NS}.{timestamp % NS:09d}: {name}: {body}"


def period(start, end, active, uid=42, gpu=3, emitted=None):
    """Create a synthetic work period independently of its emission time."""
    return event(
        end + 100 if emitted is None else emitted,
        "gpu_work_period",
        f"gpu_id={gpu} uid={uid} start_time_ns={start} end_time_ns={end} total_active_duration_ns={active}",
    )


def trace(*rows, start=START, end=END, stats=STATS, tail=(), flush=True):
    """Wrap preordered synthetic records with one window and a complete flush."""
    return "\n".join(
        [
            stats,
            event(start, "tracing_mark_write", "llm4ad_window_start"),
            *rows,
            event(end, "tracing_mark_write", "llm4ad_window_end"),
            *tail,
            *([event(end + 2 * NS, "tracing_mark_write", "llm4ad_flush_end")] if flush else []),
        ]
    )


def parse(text, **kwargs):
    """Select one synthetic UID/GPU and an explicitly confirmed raw clock."""
    return telemetry.parse_ftrace(text, uid=42, gpu_id=3, trace_clock="mono_raw", **kwargs)


def assert_invalid(result, message):
    """Require invalid evidence to carry diagnostics without numeric metrics."""
    assert result["status"] == "invalid", result
    assert result["metrics"] == {}
    assert message.lower() in " ".join(result["errors"]).lower()


def test_exact_uid_gpu_filtering_and_full_window_denominator():
    """Adjacent periods are legal, other UIDs/GPUs do not affect app totals."""
    text = trace(
        period(START, START + 100_000_000, 100_000_000, uid=142),
        period(START, START + 100_000_000, 100_000_000, gpu=30),
        period(START, START + 200_000_000, 100_000_000),
        period(START + 200_000_000, START + 500_000_000, 150_000_000),
    )
    result = parse(text)
    assert result["status"] == "ok", result
    assert result["metrics"] == {"gpu_uid_active_total_ms": 250.0, "gpu_uid_active_pct": 25.0}
    coverage = result["coverage"]["gpu_work_period"]
    assert coverage["included_periods"] == 2 and coverage["period_coverage_pct"] == 50
    assert coverage["active_pct_denominator_ns"] == NS
    assert result["provenance"]["uid"] == 42 and result["provenance"]["gpu_id"] == 3
    assert "gpu_active_ms" not in result["metrics"] and "game_fps" not in result["metrics"]


def test_boundary_periods_are_discarded_whole_with_coverage():
    """Do not invent a uniform active-time distribution across marker boundaries."""
    text = trace(
        period(START - 100_000_000, START + 100_000_000, 190_000_000),
        period(START + 100_000_000, START + 400_000_000, 120_000_000),
        tail=(period(END - 100_000_000, END + 100_000_000, 180_000_000),),
    )
    result = parse(text)
    assert result["status"] == "partial"
    assert result["metrics"]["gpu_uid_active_total_ms"] == 120
    assert result["metrics"]["gpu_uid_active_pct"] == 12
    coverage = result["coverage"]["gpu_work_period"]
    assert coverage["boundary_discarded_periods"] == 2
    assert coverage["boundary_discarded_overlap_ns"] == 200_000_000
    assert coverage["period_coverage_pct"] == 30 and coverage["uncovered_window_ns"] == 700_000_000
    assert "lower bounds" in " ".join(result["warnings"])


def test_post_window_flush_events_use_payload_interval_not_emission_timestamp():
    """Delayed emissions can describe in-window work; flush work is excluded."""
    text = trace(
        tail=(
            period(START, END, 700_000_000, emitted=END + NS),
            period(END, END + NS, 900_000_000, emitted=END + NS + 100),
        )
    )
    result = parse(text)
    assert result["status"] == "ok", result
    assert result["metrics"]["gpu_uid_active_pct"] == 70
    assert result["coverage"]["gpu_work_period"]["outside_periods"] == 1


@pytest.mark.parametrize("duration", [2 * NS, 2 * NS + 1, 3 * NS])
def test_flush_marker_confirms_at_least_two_seconds_of_post_window_tracing(duration):
    """Only the explicit flush marker confirms the retained post-window interval."""
    text = trace(period(START, START + 100, 50), flush=False)
    text += "\n" + event(END + duration, "tracing_mark_write", "llm4ad_flush_end")
    result = parse(text)
    assert result["status"] == "ok", result
    assert result["coverage"]["post_window_flush_confirmed"] is True
    assert result["coverage"]["flush_end_ns"] == END + duration
    assert result["coverage"]["post_window_flush_duration_ns"] == duration


@pytest.mark.parametrize("late_event", [False, True])
def test_missing_flush_marker_keeps_legacy_metrics_partial(late_event):
    """A long trace tail alone must not substitute for collector flush evidence."""
    text = trace(period(START, START + 100_000_000, 50_000_000), flush=False)
    if late_event:
        text += "\n" + event(END + 3 * NS, "tracing_mark_write", "unrelated")
    result = parse(text)
    assert result["status"] == "partial", result
    assert result["coverage"]["post_window_flush_confirmed"] is False
    assert "llm4ad_flush_end" in " ".join(result["warnings"])
    assert result["metrics"] == {"gpu_uid_active_total_ms": 50, "gpu_uid_active_pct": 5}


@pytest.mark.parametrize("duration", [0, NS, 2 * NS - 1])
def test_short_flush_is_invalid(duration):
    """A flush marker before two full seconds cannot prove complete collection."""
    text = trace(period(START, START + 100, 50), flush=False)
    text += "\n" + event(END + duration, "tracing_mark_write", "llm4ad_flush_end")
    result = parse(text)
    assert_invalid(result, "flush")
    assert result["coverage"]["post_window_flush_confirmed"] is False


@pytest.mark.parametrize("offset", [0, 1])
def test_duplicate_flush_markers_are_invalid(offset):
    """Identical or later duplicate markers must not be silently selected."""
    text = trace(period(START, START + 100, 50))
    text += "\n" + event(END + 2 * NS + offset, "tracing_mark_write", "llm4ad_flush_end")
    assert_invalid(parse(text), "flush")


def test_malformed_flush_timestamp_is_invalid():
    """Do not treat a malformed explicit marker as absent legacy evidence."""
    text = trace(period(START, START + 100, 50)).replace("13.000000000:", "invalid-time:")
    assert_invalid(parse(text), "timestamp")


@pytest.mark.parametrize(
    "rows",
    [
        [(0, 200, 100), (0, 200, 100)],
        [(0, 200, 100), (100, 300, 100)],
        [(200, 300, 100), (0, 100, 50)],
    ],
)
def test_duplicate_overlapping_or_reversed_periods_reject_all_metrics(rows):
    """Never deduplicate, reorder or double-count invalid per-UID periods."""
    text = trace(*(period(START + a, START + b, c, emitted=START + 1000 + i) for i, (a, b, c) in enumerate(rows)))
    assert_invalid(parse(text), "strictly increasing")


@pytest.mark.parametrize(
    "begin,finish,active",
    [
        (START, START, 0),
        (START + 1, START, 0),
        (START, END + 1, 0),
        (START, START + 100, 101),
        (START, START + 100, -1),
    ],
)
def test_invalid_duration_or_active_time_rejected(begin, finish, active):
    """Enforce the AOSP nonzero, at-most-one-second and active<=duration rules."""
    result = parse(trace(period(begin, finish, active, emitted=START + 1000)))
    assert result["status"] == "invalid" and result["metrics"] == {}


def test_zero_active_period_is_observed_zero_not_missing_measurement():
    """An explicit valid idle period supports zero, unlike absence of periods."""
    result = parse(trace(period(START, START + 100, 0)))
    assert result["status"] == "ok"
    assert result["metrics"]["gpu_uid_active_total_ms"] == 0


@pytest.mark.parametrize("stat", ["overrun", "commit overrun", "dropped events"])
def test_buffer_loss_invalidates_uid_and_device_metrics(stat):
    """Loss counters are checked before returning any aggregate."""
    text = trace(
        event(START + 1, "GPU_DVFS__Loading", "active=70, tiler=10"),
        period(START, START + 100, 50),
        stats=STATS.replace(f"{stat}: 0", f"{stat}: 1"),
    )
    assert_invalid(parse(text), "overrun or dropped")


@pytest.mark.parametrize(
    "extra",
    [
        "CPU:0 [LOST 2 EVENTS]",
        "# entries-in-buffer/entries-written: 2/9",
        "#P:2",
    ],
)
def test_other_loss_evidence_and_incomplete_per_cpu_stats_are_rejected(extra):
    """Clean counters alone cannot override explicit loss or missing CPU stats."""
    result = parse(extra + "\n" + trace(period(START, START + 100, 50)))
    assert result["status"] == "invalid" and result["metrics"] == {}


def test_stats_can_be_provided_separately_but_not_assumed():
    """Raw collector stats are required and retained through a content hash."""
    text = trace(period(START, START + 100, 50), stats="")
    assert_invalid(parse(text), "Missing or incomplete")
    result = parse(text, stats_text=STATS)
    assert result["status"] == "ok" and result["provenance"]["loss_counters"]["overrun"] == [0]
    assert len(result["provenance"]["stats_sha256"]) == 64


@pytest.mark.parametrize("clock", ["boot", "mono", "local", "mono_raw [boot]", ""])
def test_trace_clock_must_match_gpu_payload_clock(clock):
    """The caller must confirm the private instance uses CLOCK_MONOTONIC_RAW."""
    result = telemetry.parse_ftrace(trace(), uid=42, gpu_id=3, trace_clock=clock)
    assert_invalid(result, "trace_clock")


def test_embedded_clock_readback_cannot_contradict_caller_clock():
    """A collector readback selecting boot cannot be relabelled as mono_raw."""
    text = "TRACE_CLOCK=local mono_raw [boot]\n" + trace(period(START, START + 100, 50))
    assert_invalid(parse(text), "trace_clock")


def test_ambiguous_identity_fields_are_not_hidden_by_uid_filtering():
    """Duplicate UID fields must not overwrite one identity before filtering."""
    text = trace(period(START, START + 100, 50) + " uid=142", period(START + 100, START + 200, 50))
    assert_invalid(parse(text), "Duplicate fields")


@pytest.mark.parametrize("offset", [-100_000_000, 3 * NS])
def test_payload_clock_or_emission_delay_mismatch_rejected(offset):
    """Plausibility checks detect future payloads and violations of the two-second delay."""
    text = trace(period(START + offset, START + offset + 1000, 500, emitted=START + 2000))
    if offset < 0:
        text = trace(period(START - 3 * NS, START - 3 * NS + 1000, 500, emitted=START + 2000))
    assert_invalid(parse(text), "clock")


def test_microsecond_trace_precision_does_not_invent_a_clock_error():
    """Truncated printed timestamps have a known sub-microsecond uncertainty."""
    text = trace(period(START, START + 100_000_999, 500, emitted=START + 100_000_000))
    text = text.replace("10.100000000:", "10.100000:")
    assert parse(text)["status"] == "ok"


@pytest.mark.parametrize("change", ["missing", "duplicate", "reversed", "malformed", "nonmonotonic"])
def test_window_and_trace_order_validation(change):
    """Ambiguous windows and reordered traces never create valid telemetry."""
    text = trace(period(START, START + 100, 50))
    if change == "missing":
        text = text.replace("llm4ad_window_end", "unrelated_marker")
    elif change == "duplicate":
        text += "\n" + event(END + 2 * NS + 1, "tracing_mark_write", "llm4ad_window_end")
    elif change == "reversed":
        text = (
            text.replace("window_start", "window_tmp").replace("window_end", "window_start").replace("window_tmp", "window_end")
        )
    elif change == "malformed":
        text = text.replace("10.000000000:", "invalid-time:")
    else:
        text += "\n" + event(START, "tracing_mark_write", "unrelated")
    assert parse(text)["status"] == "invalid"


def test_device_loading_is_separate_from_uid_activity_and_freq_unit_is_explicit():
    """Counter writer PID is not an app UID and unconfirmed frequency stays absent."""
    text = trace(
        event(START + 1, "GPU_DVFS__Loading", "active=80, tiler=7, frag=50"),
        event(START + 2, "tracing_mark_write", "C|42|gpu_freq|600"),
        event(START + 3, "GPU_DVFS__Loading", "active=20, tiler=2"),
        period(START, START + 100_000_000, 25_000_000),
    )
    result = parse(text)
    assert result["status"] == "partial"
    assert result["metrics"]["gpu_uid_active_pct"] == 2.5
    assert result["metrics"]["gpu_device_utilization_pct"] == 50
    assert "gpu_device_frequency_mhz" not in result["metrics"]
    result = parse(text, gpu_freq_unit="MHz")
    assert result["status"] == "ok" and result["metrics"]["gpu_device_frequency_mhz"] == 600
    assert result["provenance"]["metric_sources"]["gpu_device_utilization_pct"]["scope"] == "device"
    assert "raw" not in json.dumps(result["metrics"])


def test_absent_uid_periods_do_not_become_zero_or_frames():
    """Device samples remain device-only when the requested UID is absent."""
    text = trace(event(START + 1, "GPU_DVFS__Loading", "active=60"), period(START, START + 100, 50, uid=142))
    result = parse(text)
    assert result["status"] == "partial"
    assert result["metrics"] == {"gpu_device_utilization_pct": 60}
    assert parse(trace())["status"] == "unsupported"


@pytest.mark.parametrize("active", ["101", "-1", "NaN", "40 active=50"])
def test_invalid_loading_is_not_clamped_or_averaged(active):
    """Malformed device percentages invalidate telemetry rather than looking plausible."""
    result = parse(trace(event(START + 1, "GPU_DVFS__Loading", f"active={active}")))
    assert result["status"] == "invalid"


def test_sysfs_samples_use_the_same_half_open_marker_window():
    """Only included device samples contribute; they cannot supply per-app activity."""
    samples = [
        {"timestamp_ns": START - 1, "gpu_utilization": "100 0 0"},
        {"timestamp_ns": START, "gpu_utilization": "20 0 80", "current_freqency": "2 400000", "battery_temp": "310"},
        {
            "timestamp_ns": START + 500_000_000,
            "gpu_utilization": "60 0 40",
            "current_freqency": "1 600000",
            "battery_temp": "330",
        },
        {"timestamp_ns": END, "gpu_utilization": "100 0 0"},
    ]
    result = telemetry.parse_sysfs_samples(samples, window_start_ns=START, window_end_ns=END, frequency_unit="kHz")
    assert result["status"] == "ok", result
    assert result["metrics"] == {"gpu_device_utilization_pct": 40, "gpu_device_frequency_mhz": 500, "battery_temperature_c": 32}
    assert result["coverage"]["included_samples"] == 2 and result["coverage"]["outside_samples"] == 2
    assert all(source["scope"] == "device" for source in result["provenance"]["metric_sources"].values())


def test_missing_sysfs_nodes_and_unconfirmed_frequency_are_not_filled():
    """No guessed frequency unit, zero utilization, app timing or FPS is returned."""
    samples = [{"timestamp_ns": START, "gpu_utilization": None, "current_freqency": "1 500000", "battery_temp": None}]
    result = telemetry.parse_sysfs_samples(samples, window_start_ns=START, window_end_ns=END)
    assert result["status"] == "unsupported" and result["metrics"] == {}
    assert result["coverage"]["unconverted_frequency_samples"] == 1


@pytest.mark.parametrize(
    "samples",
    [
        [{"timestamp_ns": START}, {"timestamp_ns": START}],
        [{"timestamp_ns": START + 1}, {"timestamp_ns": START}],
        [{"timestamp_ns": True}],
        [{"timestamp_ns": START, "gpu_utilization": "101 0 0"}],
        [{"timestamp_ns": START, "gpu_utilization": "50"}],
        [{"timestamp_ns": START, "current_freqency": "500000"}],
        [{"timestamp_ns": START, "current_freqency": "1 -500000"}],
        [{"timestamp_ns": START, "battery_temp": "NaN"}],
    ],
)
def test_bad_sysfs_samples_are_explicitly_invalid(samples):
    """Invalid, repeated or unordered samples are not silently repaired."""
    result = telemetry.parse_sysfs_samples(samples, window_start_ns=START, window_end_ns=END)
    assert result["status"] == "invalid" and result["metrics"] == {}


@pytest.mark.parametrize("unit,value", [("Hz", 500_000_000), ("kHz", 500_000), ("MHz", 500)])
def test_confirmed_frequency_units_are_converted_without_magnitude_guessing(unit, value):
    """Trace and sysfs frequency values use independent caller-confirmed units."""
    result = parse(trace(event(START + 1, "tracing_mark_write", f"C|9|gpu_freq|{value}")), gpu_freq_unit=unit)
    assert result["metrics"]["gpu_device_frequency_mhz"] == 500
    result = telemetry.parse_sysfs_samples(
        [{"timestamp_ns": START, "current_freqency": f"2 {value}"}],
        window_start_ns=START,
        window_end_ns=END,
        frequency_unit=unit,
    )
    assert result["metrics"]["gpu_device_frequency_mhz"] == 500


def test_parsers_are_pure_and_return_json_serializable_evidence():
    """Inputs are retained by the caller; parsers emit hashes, not copied raw evidence."""
    samples = [{"timestamp_ns": START, "gpu_utilization": "40 0 60"}]
    before = json.dumps(samples)
    result = telemetry.parse_sysfs_samples(samples, window_start_ns=START, window_end_ns=END)
    assert json.dumps(samples) == before
    encoded = json.dumps(result, allow_nan=False)
    assert "samples_sha256" in encoded and "40 0 60" not in encoded
    assert not any(name in result["metrics"] for name in ("gpu_active_ms", "game_fps", "gpu_uid_active_pct"))
