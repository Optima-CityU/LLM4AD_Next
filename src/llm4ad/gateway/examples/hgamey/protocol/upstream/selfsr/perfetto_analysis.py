"""Analyze an explicitly marked A/B window with the official Trace Processor."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import statistics
from pathlib import Path


def percentile(values: list[float], fraction: float) -> float:
    """Interpolate one quantile from actual observations."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def frame_summary(rows: list[dict], start: int, end: int) -> dict:
    """Distinguish application records from actual linked display presentations."""
    result = {"status": "unavailable", "metrics": {}, "application_records": len(rows)}
    layers = sorted({row["layer_name"] for row in rows if row.get("layer_name")})
    result["layers"] = layers
    if len(layers) != 1:
        result["reason"] = "No unique target application layer in the marked window"
        return result
    tokens = {}
    for row in rows:
        token = row.get("display_frame_token")
        ts = row.get("present_ts")
        if row.get("display_matches") != 1 or not token or ts is None or not start <= ts < end:
            continue
        if row.get("present_type") == "Dropped Frame":
            continue
        tokens[token] = ts
    presented = sorted(set(tokens.values()))
    result.update(linked_presentations=len(presented), timestamp_source="linked SurfaceFlinger actual display-frame end")
    if len(presented) < 2:
        result["reason"] = "Too few unambiguous linked display presentations; retain native frame evidence"
        return result
    intervals = [(b - a) / 1e6 for a, b in zip(presented, presented[1:], strict=False)]
    result["status"] = "measured"
    result["metrics"] = {
        "target_present_fps": (len(presented) - 1) * 1e9 / (presented[-1] - presented[0]),
        "target_present_interval_p50_ms": statistics.median(intervals),
        "target_present_interval_p95_ms": percentile(intervals, 0.95),
        "target_present_interval_max_ms": max(intervals),
    }
    result["jank_types"] = {
        kind: sum(row.get("jank_type") == kind for row in rows) for kind in sorted({str(row.get("jank_type")) for row in rows})
    }
    return result


def analyze(processor, marker: str, package: str, seconds: float | None = None) -> dict:
    """Query only target-process observations within one complete ATrace marker.

    Args:
        processor: Official TraceProcessor instance or a SQL-compatible test fixture.
        marker: Unique owned ATrace window label.
        package: Exact target process package.
        seconds: Requested native window duration, when known.

    Returns:
        JSON-safe provenance and measured observations, without a power score.
    """
    if not re.fullmatch(r"llm4ad_window_[a-f0-9]{32}", marker) or not re.fullmatch(r"[A-Za-z0-9_.]+", package):
        raise ValueError("Invalid marker or package")
    result = {
        "schema": "llm4ad/perfetto-analysis@1",
        "status": "unavailable",
        "package": package,
        "marker": marker,
        "metrics": {},
        "query_errors": {},
        "energy_used_for_fitness": False,
    }

    def query(name, sql):
        try:
            return [vars(row) for row in processor.query(sql)]
        except Exception as error:
            result["query_errors"][name] = str(error)
            return []

    windows = query("window", f"SELECT ts, dur FROM slice WHERE name='{marker}'")
    if len(windows) != 1 or windows[0]["dur"] <= 0:
        result["reason"] = "A unique complete measurement window is unavailable"
        return result
    start, end = windows[0]["ts"], windows[0]["ts"] + windows[0]["dur"]
    result["window"] = {
        "start_ns": start,
        "end_ns": end,
        "duration_ns": end - start,
        "clock": "Trace Processor normalized trace time",
    }
    if seconds is not None and end - start < seconds * 1e9:
        result["reason"] = "The marked interval ended before the requested measurement duration"
        return result
    result["trace_errors"] = query(
        "trace_errors", "SELECT name, severity, value FROM stats WHERE value>0 AND severity IN ('error','data_loss')"
    )
    result["counter_tracks"] = query(
        "counters",
        f"""
        SELECT t.name, t.unit, COUNT(*) AS samples, MIN(c.value) AS minimum,
               MAX(c.value) AS maximum, AVG(c.value) AS sample_mean
        FROM counter c JOIN counter_track t ON t.id=c.track_id
        WHERE c.ts >= {start} AND c.ts < {end}
        GROUP BY t.id ORDER BY t.name""",
    )
    gpu = [row for row in result["counter_tracks"] if "gpu" in row["name"].lower()]
    result["gpu"] = {
        "status": "observed" if gpu else "unavailable",
        "counter_tracks": gpu,
        "reason": "Device counter observations; application UID active time remains in native telemetry",
    }
    frames = query(
        "frames",
        f"""
        WITH displays AS (
          SELECT d.display_frame_token, COUNT(*) AS display_matches,
                 MAX(d.ts+d.dur) AS present_ts
          FROM actual_frame_timeline_slice d JOIN process p ON p.upid=d.upid
          WHERE p.name='/system/bin/surfaceflinger' AND d.surface_frame_token IS NULL
            AND d.display_frame_token>0 AND d.dur>0 AND d.present_type!='Dropped Frame'
          GROUP BY d.display_frame_token
        )
        SELECT a.layer_name, a.display_frame_token, a.surface_frame_token,
               a.present_type, a.jank_type, d.present_ts, d.display_matches
        FROM actual_frame_timeline_slice a JOIN process p ON p.upid=a.upid
        LEFT JOIN displays d ON a.display_frame_token=d.display_frame_token
        WHERE p.name='{package}' AND a.ts >= {start} AND a.ts < {end} AND a.dur>0""",
    )
    result["frames"] = frame_summary(frames, start, end)
    cpu = query(
        "cpu",
        f"""
        SELECT SUM(MIN(s.ts+s.dur,{end})-MAX(s.ts,{start})) AS running_ns,
               COUNT(*) AS slices
        FROM sched s JOIN thread t ON s.utid=t.utid JOIN process p ON p.upid=t.upid
        WHERE p.name='{package}' AND s.dur>0 AND s.ts<{end} AND s.ts+s.dur>{start}""",
    )
    result["cpu"] = cpu
    if cpu and cpu[0]["slices"] > 0:
        result["metrics"]["target_cpu_running_ms"] = cpu[0]["running_ns"] / 1e6
    result["metrics"].update(result["frames"]["metrics"])
    battery = [row for row in result["counter_tracks"] if row["name"].startswith("batt.")]
    voltage = [row for row in battery if row["name"] == "batt.voltage_uv"]
    result["battery"] = {
        "status": "observed" if battery else "unavailable",
        "tracks": battery,
        "power_scoring_enabled": False,
        "reason": "Raw standard battery observations; pack scope and hardware calibration are not established",
    }
    if voltage and any(not 1_000_000 <= row["minimum"] <= row["maximum"] <= 30_000_000 for row in voltage):
        result["battery"].update(
            status="invalid", reason="Standard voltage counter violates its declared microvolt units; no rescaling applied"
        )
    if any(row["severity"] == "data_loss" or "overrun" in row["name"] for row in result["trace_errors"]):
        result["metrics"] = {}
        result["reason"] = "Trace loss prevents accepting the derived measurements"
    elif result["metrics"]:
        result["status"] = "measured"
    return result


def main():
    """Analyze a local trace without downloading runtime binaries."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--seconds", type=float)
    args = parser.parse_args()
    from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

    binary = os.environ.get("TRACE_PROCESSOR_SHELL") or shutil.which("trace_processor_shell")
    if not binary or not Path(binary).is_file():
        raise RuntimeError("Install the pinned Trace Processor in the evaluator image; runtime downloads are disabled")
    config = TraceProcessorConfig(bin_path=binary, load_timeout=30)
    with TraceProcessor(trace=str(args.trace), config=config) as processor:
        result = analyze(processor, args.marker, args.package, args.seconds)
    result["trace"] = {
        "name": args.trace.name,
        "bytes": args.trace.stat().st_size,
        "sha256": hashlib.sha256(args.trace.read_bytes()).hexdigest(),
    }
    import importlib.metadata

    result["processor"] = {"sdk_version": importlib.metadata.version("perfetto")}
    args.trace.with_name("analysis.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
