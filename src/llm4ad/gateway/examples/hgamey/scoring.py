"""Weighted real-game fitness with automatic penalties for quality gaps."""

from __future__ import annotations

import copy
import math

DEFAULT_GAME_SCORING = {
    "schema": "llm4ad/hgamey-game-scoring@1",
    "weights": {"fps": 0.25, "frame_p95": 0.35, "gpu_per_frame": 0.20, "quality": 0.20},
    "quality_gap_score": -100.0,
    "sources": {
        "fps": "display_fps",
        "frame_p95": "display_frame_p95_upper_ms",
        "gpu_per_frame": {"activity": "gpu_uid_active_pct", "fps": "display_fps"},
        "quality": "quality.metrics",
    },
    "scales": {"fps": 10.0, "frame_p95": 10.0, "gpu_per_frame": 10.0, "quality": 1.0},
    "quality_scales": {"stable_psnr_db": 1.0, "stable_dssim": 0.001, "detail_log2": 0.15},
}
_DIRECTIONS = {"stable_psnr_db": "higher", "stable_dssim": "lower", "detail_log2": "near_zero"}
_FRAME_QUALITY_PROTOCOL = "same-input-gpu-replay@1"


def _finite(value: object) -> bool:
    """Identify finite real measurements, excluding boolean values."""
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def normalize_game_scoring(value: dict | None) -> dict:
    """Validate and copy a policy, normalizing its fixed weights to sum to one.

    Args:
        value: Optional partial policy overriding the defaults. A finite negative
            quality_gap_score caps fitness for unverified or degraded quality.
            Performance source names refer to baseline_/comparison_ keys in report.metrics.
            The quality source is a dotted path to official IQ metric rows.

    Returns:
        A JSON-serializable policy containing all sources, scales, weights and
        the quality gap score, which is independent of weight normalization.
        Repeated normalization preserves the policy's identity and values.

    Raises:
        ValueError: The policy has unknown fields, invalid sources, nonfinite or
            negative weights, no positive weight, nonpositive scales, or a
            quality gap score that is not finite and strictly negative.
    """
    if value is not None and (not isinstance(value, dict) or value.keys() - DEFAULT_GAME_SCORING.keys()):
        raise ValueError("Unsupported game scoring policy")
    policy = copy.deepcopy(DEFAULT_GAME_SCORING)
    for name, override in (value or {}).items():
        if isinstance(policy[name], dict):
            if not isinstance(override, dict) or override.keys() - policy[name].keys():
                raise ValueError(f"Unsupported game scoring {name}")
            policy[name].update(copy.deepcopy(override))
        else:
            policy[name] = override
    if policy["schema"] != DEFAULT_GAME_SCORING["schema"]:
        raise ValueError("Unsupported game scoring schema")
    if not _finite(policy["quality_gap_score"]) or policy["quality_gap_score"] >= 0:
        raise ValueError("Game scoring quality_gap_score must be finite and negative")
    weights = policy["weights"]
    if any(not _finite(w) or w < 0 for w in weights.values()) or not any(weights.values()):
        raise ValueError("Game scoring weights must be finite, nonnegative and have a positive sum")
    # Scaling first also supports finite weights whose unscaled sum overflows.
    maximum = max(weights.values())
    total = math.fsum(w / maximum for w in weights.values())
    if maximum > 1 or math.fsum(weights.values()) != 1.0:
        policy["weights"] = {key: (weight / maximum) / total for key, weight in weights.items()}
        largest = max(weights, key=weights.get)
        policy["weights"][largest] = 1.0 - math.fsum(w for key, w in policy["weights"].items() if key != largest)
    for group in ("scales", "quality_scales"):
        if any(not _finite(s) or s <= 0 for s in policy[group].values()):
            raise ValueError(f"Game scoring {group} must be finite and positive")
    sources = policy["sources"]
    gpu = sources["gpu_per_frame"]
    if not isinstance(gpu, dict) or set(gpu) != {"activity", "fps"}:
        raise ValueError("GPU sources require activity and fps metric names")
    names = [sources["fps"], sources["frame_p95"], sources["quality"], *gpu.values()]
    if any(not isinstance(name, str) or not name or any(not part for part in name.split(".")) for name in names):
        raise ValueError("Game scoring sources must be nonempty metric paths")
    return policy


def _quality(rows: object, scales: dict) -> tuple[float | None, dict, list[str]]:
    """Compute only penalties beyond official self-noise reference lines."""
    details, missing = {}, []
    for key, direction in _DIRECTIONS.items():
        matches = [row for row in rows if isinstance(row, dict) and row.get("key") == key] if isinstance(rows, list) else []
        row = matches[0] if len(matches) == 1 else {}
        values = [row.get(field) for field in ("value", "native_self", "ref_line")]
        if row.get("better") != direction or not all(_finite(v) for v in values):
            missing.append(f"quality.{key}: missing finite official value, native_self or ref_line")
            continue
        measured, native_self, reference = values
        if native_self < 0 or reference < 0 or (direction != "near_zero" and measured < 0):
            missing.append(f"quality.{key}: invalid negative measurement or reference")
            continue
        gap = (reference - measured if direction == "higher"
               else (abs(measured) if direction == "near_zero" else measured) - reference)
        penalty = max(0.0, gap) / scales[key]
        if not _finite(penalty):
            missing.append(f"quality.{key}: scaled reference gap is nonfinite")
            continue
        details[key] = dict(row, gap_beyond_ref=max(0.0, gap), scale=scales[key], penalty=penalty)
    if missing:
        return None, details, missing
    try:
        gain = -math.fsum(item["penalty"] / len(_DIRECTIONS) for item in details.values())
    except OverflowError:
        gain = None
    if not _finite(gain):
        return None, details, ["quality: aggregate reference penalty is nonfinite"]
    return gain, details, []


def _frame_quality(quality: dict, scales: dict) -> tuple[float | None, dict, list[str]]:
    """Average complete frame penalties while retaining every frame's metric details.

    Args:
        quality: Same-input replay evidence containing all required frame records.
        scales: Frozen normalization scales for the three official IQ metrics.

    Returns:
        The equal mean of frame gains, flattened metric details, and diagnostics.
        Any incomplete or ambiguous frame makes the aggregate unavailable.
    """
    frames = quality.get("frames")
    if not isinstance(frames, list) or not frames:
        return None, {}, ["quality.frames: requires a nonempty list of measured replay frames"]
    details, missing, gains, seen = {}, [], [], set()
    if quality.get("protocol") != _FRAME_QUALITY_PROTOCOL:
        missing.append("quality.protocol: unsupported or missing same-input GPU replay protocol")
    required = quality.get("required_frames", len(frames))
    if type(required) is not int or required != len(frames):
        missing.append("quality.required_frames: replay frame count is invalid or incomplete")
    if (
        quality.get("status") != "measured"
        or not isinstance(quality.get("not_computed"), list)
        or quality.get("not_computed")
    ):
        missing.append("quality: replay is not completely measured")
    for index, frame in enumerate(frames):
        frame = frame if isinstance(frame, dict) else {}
        identity = frame.get("frame_id")
        valid_identity = isinstance(identity, str) and bool(identity.strip())
        prefix = identity if valid_identity else f"frames[{index}]"
        frame_missing = []
        if not valid_identity or identity in seen:
            frame_missing.append(f"quality.frames[{index}].frame_id: requires a unique nonempty string")
        else:
            seen.add(identity)
        if (
            frame.get("status") != "measured"
            or not isinstance(frame.get("not_computed"), list)
            or frame.get("not_computed")
        ):
            frame_missing.append(f"quality.{prefix}: frame is not completely measured")
        rows = frame.get("metrics")
        gain, measured, errors = _quality(rows, scales)
        frame_missing.extend(f"quality.{prefix}: {error}" for error in errors)
        if not isinstance(rows, list) or len(rows) != len(_DIRECTIONS) or any(
            not isinstance(row, dict) or not isinstance(row.get("key"), str) or row["key"] not in _DIRECTIONS
            for row in rows
        ):
            frame_missing.append(f"quality.{prefix}: requires exactly three official metric rows")
        for key, direction in _DIRECTIONS.items():
            detail = dict(measured.get(key, {
                "key": key, "better": direction, "value": None, "native_self": None, "ref_line": None,
                "gap_beyond_ref": None, "scale": scales[key], "penalty": None,
            }))
            detail.update(frame_id=identity if valid_identity else None,
                          status="unverified" if frame_missing else "measured",
                          provenance=copy.deepcopy(frame.get("provenance")))
            name = f"{prefix}.{key}"
            while name in details:
                name = f"frames[{index}].{name}"
            details[name] = detail
        missing.extend(frame_missing)
        if gain is not None:
            gains.append(gain)
    if missing:
        return None, details, missing
    try:
        gain = math.fsum(value / len(frames) for value in gains)
    except OverflowError:
        gain = None
    if not _finite(gain):
        return None, details, ["quality: aggregate frame penalty is nonfinite"]
    return gain, details, []


def score_game(report: dict, policy: dict) -> dict:
    """Score measured A/B gains and official quality gaps using fixed weights.

    Args:
        report: A game-ab@1 report with baseline/comparison performance metrics
            and legacy official IQ rows or same-input GPU replay frame records.
            Invalid data is reported as missing.
        policy: A policy from normalize_game_scoring; raw policies are accepted.
            Percentage gains are divided by their configurable scales. Quality
            averages three scaled reference-line excesses per frame, then gives
            every required frame equal weight. It never gives a sharpening reward.
            Scales are scoring units, not validity thresholds.

    Returns:
        A dictionary with score, unpenalized_score, quality_assessment, metrics,
        components and missing. Each component explains its source, gain, scale,
        weight and weighted contribution. Unverified or beyond-reference quality
        caps valid fitness at quality_gap_score. Missing quality retains a None
        contribution and unpenalized_score, plus diagnostics, without blocking
        scoring or reweighting. Missing positive-weight performance still yields
        score=None. A zero quality weight skips its assessment and penalty.
        GPU activity per presented frame is 10*UID_active_pct/display_fps in ms;
        it is UID activity rather than shader execution duration. Presentation
        P95 defaults to a histogram upper bound; precise Perfetto sources must
        be selected explicitly in the policy before evaluating candidates.
    """
    policy = normalize_game_scoring(policy)
    result = {
        "score": None, "unpenalized_score": None, "metrics": {}, "components": {}, "missing": [],
        "quality_assessment": {
            "status": "not_scored", "penalized": False,
            "penalty_score": policy["quality_gap_score"], "reasons": [],
        },
    }
    quality_missing = []
    report = report if isinstance(report, dict) else {}
    measured = report.get("metrics", {})
    measured = measured if isinstance(measured, dict) else {}
    for name, weight in policy["weights"].items():
        if weight == 0:
            continue
        source, scale = policy["sources"][name], policy["scales"][name]
        component = {"weight": weight, "scale": scale, "source": copy.deepcopy(source), "contribution": None}
        result["components"][name] = component
        if name == "quality":
            rows = report
            parent = None
            for part in source.split("."):
                parent = rows
                rows = rows.get(part) if isinstance(rows, dict) else None
            if isinstance(parent, dict) and ("frames" in parent or parent.get("protocol") == _FRAME_QUALITY_PROTOCOL):
                gain, details, missing = _frame_quality(parent, policy["quality_scales"])
                component["source"] = source.rpartition(".")[0] + ".frames" if "." in source else "frames"
                component["formula"] = "-mean(per-frame mean(max(0, gap beyond official reference line) / quality scale))"
                result["quality_assessment"].update(
                    protocol=parent.get("protocol") if isinstance(parent.get("protocol"), str) else None,
                    scope="same_input_gpu_replay",
                )
            else:
                gain, details, missing = _quality(rows, policy["quality_scales"])
                if isinstance(parent, dict) and (parent.get("status") != "measured" or parent.get("not_computed")):
                    missing.append("quality: official IQ is not completely measured")
                    gain = None
                component["formula"] = "-mean(max(0, gap beyond official reference line) / quality scale)"
            result["metrics"][name] = details
            result["missing"].extend(missing)
            quality_missing.extend(missing)
        else:
            values = []
            for side in ("baseline", "comparison"):
                if name == "gpu_per_frame":
                    activity, fps = (measured.get(f"{side}_{source[key]}") for key in ("activity", "fps"))
                    value = 10 * (activity / fps) if _finite(activity) and activity >= 0 and _finite(fps) and fps > 0 else None
                else:
                    value = measured.get(f"{side}_{source}")
                values.append(value if _finite(value) else None)
            baseline, candidate = values
            valid = all(_finite(v) and v >= 0 for v in values) and baseline > 0
            gain = (candidate / baseline - 1) * (100 if name == "fps" else -100) if valid else None
            if name != "fps" and baseline == candidate == 0:
                gain = 0.0
            if not _finite(gain):
                gain = None
            result["metrics"][name] = {"baseline": baseline, "candidate": candidate, "gain_pct": gain}
            if name == "gpu_per_frame":
                component["label"] = "Application UID GPU active ms per presented frame"
                component["formula"] = "(1 - candidate/baseline) * 100; ms/frame = 10 * gpu_uid_active_pct / display_fps"
            else:
                component["formula"] = "(candidate/baseline - 1) * 100" if name == "fps" else "(1 - candidate/baseline) * 100"
                component["label"] = (
                    "Display presented frames per second" if name == "fps"
                    else "Application presentation P95 histogram upper bound (ms)" if source == "display_frame_p95_upper_ms"
                    else f"Policy-selected P95 (ms): {source}"
                )
            if gain is None:
                result["missing"].append(f"{name}: requires finite nonnegative A/B data and a positive baseline from {source}")
        if gain is not None and _finite(gain / scale) and _finite(weight * (gain / scale)):
            component.update(gain=gain, normalized=gain / scale, contribution=weight * (gain / scale))
        elif gain is not None:
            reason = f"{name}: normalized contribution is nonfinite"
            result["missing"].append(reason)
            if name == "quality":
                quality_missing.append(reason)
    assessment = result["quality_assessment"]
    quality = result["components"].get("quality")
    if quality is not None:
        reasons = quality_missing or [
            f"quality.{key}: exceeds official reference line (scaled penalty {item['penalty']})"
            for key, item in result["metrics"]["quality"].items() if item["gap_beyond_ref"] > 0
        ]
        assessment.update(
            status="unverified" if quality_missing else "beyond_reference" if reasons else "within_reference",
            penalized=bool(reasons), reasons=reasons,
        )
    performance = [item["contribution"] for name, item in result["components"].items() if name != "quality"]
    if any(value is None for value in performance):
        return result
    try:
        performance_score = math.fsum(performance)
    except OverflowError:
        performance_score = None
    if not _finite(performance_score):
        result["missing"].append("score: weighted performance sum is nonfinite")
        return result
    if quality_missing:
        result["score"] = policy["quality_gap_score"]
        return result
    try:
        score = math.fsum(item["contribution"] for item in result["components"].values())
    except OverflowError:
        score = None
    if not _finite(score):
        reason = "quality: weighted score is nonfinite"
        result["missing"].append(reason)
        assessment.update(status="unverified", penalized=True, reasons=[reason])
        result["score"] = policy["quality_gap_score"]
        return result
    result["unpenalized_score"] = score
    result["score"] = min(score, policy["quality_gap_score"]) if assessment["penalized"] else score
    return result
