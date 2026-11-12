"""Parse application-layer presentation histograms without claiming GPU timing."""

from __future__ import annotations

import math
import re

# AOSP TimeStatsHelper::Histogram::insert/toString at this fixed revision:
# https://android.googlesource.com/platform/frameworks/native/+/d34795a625e9e286d277e8ce35e0961a744b87bc/services/surfaceflinger/TimeStats/timestatsproto/TimeStatsHelper.cpp
_BUCKETS_MS = (*range(35), *range(36, 51, 2), *range(54, 151, 4), *range(200, 1001, 50))


def parse_frame_stats(text: str, package: str, uid: int) -> dict:
    """Read one exact application's active SurfaceView histogram.

    Args:
        text: The raw SurfaceFlinger TimeStats dump from this sampling window.
        package: Exact Android package being measured.
        uid: The verified UID of that package.

    Returns:
        Presentation metrics and provenance, or an unavailable reason. Histogram
        bounds describe a quantized percentile, not an exact GPU frame duration.
        Only the complete supported AOSP bucket layout is accepted.
    """
    matches = []
    for block in re.split(r"(?m)(?=^uid = )", text):
        owner = re.search(r"(?m)^uid = ([0-9]+)$", block)
        layer = re.search(r"(?m)^layerName = (.+)$", block)
        frames = re.search(r"(?m)^totalFrames = ([0-9]+)$", block)
        if (
            owner
            and int(owner[1]) == uid
            and layer
            and layer[1].startswith(f"SurfaceView[{package}/")
            and "(BLAST)" in layer[1]
            and "selfsr_interp" not in layer[1]
            and frames
            and int(frames[1]) > 0
        ):
            matches.append((block, layer[1]))
    if len(matches) != 1:
        return {"metrics": {}, "unavailable_reason": "No unique active application SurfaceView histogram"}
    block, layer = matches[0]
    if len(re.findall(r"(?m)^present2present histogram is as below:", block)) > 1:
        return {"metrics": {}, "unavailable_reason": "Duplicate application presentation histograms"}
    histogram = re.search(r"(?m)^present2present histogram is as below:\n([^\n]+)", block)
    if histogram is None:
        return {"metrics": {}, "unavailable_reason": "Application presentation histogram is unavailable"}
    tokens = histogram[1].split()
    if not tokens or any(not re.fullmatch(r"[0-9]+ms=[0-9]+", token) for token in tokens):
        return {"metrics": {}, "unavailable_reason": "Invalid application presentation histogram"}
    buckets = [tuple(map(int, re.findall(r"[0-9]+", token))) for token in tokens]
    if any(a[0] >= b[0] for a, b in zip(buckets, buckets[1:], strict=False)):
        return {"metrics": {}, "unavailable_reason": "Unordered application presentation histogram"}
    if tuple(bucket for bucket, _ in buckets) != _BUCKETS_MS:
        return {"metrics": {}, "unavailable_reason": "Incomplete or unsupported application histogram buckets"}
    count = sum(n for _, n in buckets)
    frame_count = int(re.search(r"(?m)^totalFrames = ([0-9]+)$", block)[1])
    if count > frame_count:
        return {"metrics": {}, "unavailable_reason": "Presentation intervals exceed the layer frame count"}
    if count == 0:
        return {"metrics": {}, "unavailable_reason": "No application presentation intervals were recorded"}
    rank, cumulative = math.ceil(count * 0.95), 0
    metrics = {}
    for index, (bucket, n) in enumerate(buckets):
        cumulative += n
        if cumulative >= rank:
            # TimeStats::msBetween truncates to integer milliseconds, then
            # Histogram::insert rounds up to the configured bucket. The final
            # bucket also includes all overflow durations.
            metrics["display_frame_p95_lower_ms"] = buckets[index - 1][0] + 1 if index else 0
            if index + 1 < len(buckets):
                metrics["display_frame_p95_upper_ms"] = bucket + 1
            break
    dropped = re.search(r"(?m)^droppedFrames = ([0-9]+)$", block)
    if dropped:
        metrics["display_dropped_frames"] = int(dropped[1])
    return {
        "metrics": metrics,
        "provenance": {
            "source": "SurfaceFlinger TimeStats present2present histogram",
            "package": package,
            "uid": uid,
            "layer": layer,
            "interval_samples": count,
            "percentile": 0.95,
            "bounds": "lower inclusive, upper exclusive; final histogram bucket has no finite upper bound",
            "quantization": "integer milliseconds truncated, then ceiling to AOSP histogram bucket",
            "is_gpu_execution_time": False,
        },
    }
