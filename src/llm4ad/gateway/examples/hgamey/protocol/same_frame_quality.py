"""Evaluate captured game frames using actual same-input baseline and candidate GPU outputs."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from pathlib import Path

from llm4ad.gateway.sdk.storage import persist

from . import game_quality, quality_replay


def _prefix(frame: dict, prefix: str) -> dict:
    """Qualify one native replay's local artifact paths under the game evidence root."""
    frame = copy.deepcopy(frame)
    for item in [frame.get("source", {}), *frame.get("baseline", []), *frame.get("candidate", [])]:
        if item.get("artifact"):
            item["artifact"] = f"{prefix}/{item['artifact']}"
    for item in frame.get("artifacts", []):
        item["name"] = f"{prefix}/{item['name']}"
    for group in frame.get("evidence_comparisons", []):
        group["id"] = prefix.replace("/", "-") + "-" + group["id"]
        group["label"] = f"Same-input GPU quality: {frame['frame_id']}"
        group["kind"] = "static"
        group["scope"] = "same-input-gpu-replay@1"
        for item in group.get("images", []):
            item["artifact"] = f"{prefix}/{item['artifact']}"
    return frame


def measure(folder: Path, frozen: dict, *, deadline: float, phonefarm: str) -> dict:
    """Replay one recorded baseline frame per game pair after performance collection.

    Args:
        folder: Owned executor job containing frozen shaders and game evidence.
        frozen: Accepted submission and verified physical-device identity.
        deadline: Remaining monotonic deadline shared with the original game job.
        phonefarm: Installed native runner used for GPU evaluation and official IQ.

    Returns:
        Official per-frame IQ, owned output artifacts and device-cleanup status.
        Replay timing is diagnostic and never enters game performance metrics.
    """
    evidence = folder / "evidence"
    options = frozen["submission"]["options"]
    frames, artifacts, comparisons = [], [], []
    cleanup_confirmed = True
    restoration_path = evidence / "quality/replay/restoration.json"
    restoration = {"job_id": folder.name, "restored": True, "pending_frame": None}
    persist(restoration_path, restoration)
    arms = [json.loads(line) for line in (evidence / "arms.jsonl").read_text().splitlines()]
    screenshots, _ = game_quality.screenshot_artifacts(evidence, options, arms)
    available = {item["name"]: item for item in screenshots}

    def cancelled():
        return json.loads((folder / "job.json").read_text()).get("state") == "cancel_requested"

    for pair in range(1, options["rounds"] + 1):
        frame_id = f"frame-{pair:02}"
        record = {"frame_id": frame_id, "status": "not_measured", "metrics": [], "not_computed": []}
        if not cleanup_confirmed:
            record["not_computed"].append("Prior replay restoration is unconfirmed")
            frames.append(record)
            continue
        external_pending = False
        try:
            if cancelled() or time.monotonic() >= deadline:
                raise RuntimeError("Quality replay cancelled or experiment deadline exceeded")
            selected = [arm for arm in arms if arm.get("arm") == f"A{pair}" and arm.get("pair") == pair]
            if len(selected) != 1 or selected[0].get("in_game") is not True or selected[0].get("error"):
                raise ValueError("No valid baseline game capture exists for this pair")
            captures = selected[0].get("quality_samples", {}).get("captures", [])
            if not captures:
                raise ValueError("No recorded quality input frame exists for this pair")
            source = evidence / "shots" / Path(captures[0]).name
            if Path(captures[0]).absolute() != source.absolute() or not re.fullmatch(
                rf"A{pair}(?:#r[1-9][0-9]*)?_quality_1\.png", source.name
            ):
                raise ValueError("Replay input belongs to another arm or capture")
            item = available.get(source.relative_to(evidence).as_posix())
            if item is None or hashlib.sha256(game_quality._image(source)).hexdigest() != item["sha256"]:
                raise ValueError("Captured replay input differs from the recorded game evidence")
            directory = evidence / "quality/replay" / frame_id
            print(f"Same-input GPU quality replay: {frame_id} of {options['rounds']} started", flush=True)
            external_pending = True
            restoration.update(restored=False, pending_frame=frame_id)
            persist(restoration_path, restoration)
            native = quality_replay.replay_frame(
                directory, source, options["quality_replay"], frozen["device"]["endpoint"],
                cancelled=cancelled, timeout=max(0.0, deadline - time.monotonic()),
            )
            external_pending = False
            cleanup_confirmed = native.get("cleanup_confirmed") is True
            restoration.update(restored=cleanup_confirmed, pending_frame=None if cleanup_confirmed else frame_id)
            persist(restoration_path, restoration)
            native["frame_id"] = frame_id
            native = _prefix(native, directory.relative_to(evidence).as_posix())
            native["capture"] = dict(item, arm=f"A{pair}", pair=pair)
            frames.append(native)
            artifacts.extend(native.get("artifacts", []))
            comparisons.extend(native.get("evidence_comparisons", []))
            print(f"Same-input GPU quality replay: {frame_id} {native.get('status', 'unavailable')}; "
                  f"cleanup confirmed={cleanup_confirmed}", flush=True)
        except Exception as error:
            if external_pending:
                cleanup_confirmed = False
            record["not_computed"].append(f"{type(error).__name__}: {error}")
            frames.append(record)
    quality = game_quality.judge_replay(evidence, frames, phonefarm)
    print(f"Official per-frame IQ: {quality['status']}", flush=True)
    quality.update(
        protocol="same-input-gpu-replay@1", required_frames=options["rounds"],
        scope="operator_quality_on_replayed_real_game_captures",
        performance_window="excluded; measured after restored game A/B windows",
    )
    return {"quality": quality, "artifacts": artifacts, "evidence_comparisons": comparisons,
            "cleanup_confirmed": cleanup_confirmed, "frames": frames}
