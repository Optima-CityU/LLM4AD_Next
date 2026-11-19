"""Retain real game captures and official, self-noise-relative IQ measurements."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
import subprocess
import zlib
from pathlib import Path

IQ_KEYS = {"stable_psnr_db": "higher", "stable_dssim": "lower", "detail_log2": "near_zero"}
MAX_IMAGE_BYTES = 64 * 1024 * 1024
REPLAY_PROTOCOL = "same-input-gpu-replay@1"


def _reject_constant(value: str) -> None:
    """Reject nonstandard JSON constants in official measurement output."""
    raise ValueError(f"Nonfinite official IQ value: {value}")


def _image(path: Path) -> bytes:
    """Read a bounded real PNG without following filesystem aliases."""
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("Screenshot path contains a symbolic link")
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
        raise ValueError("Screenshot is missing, empty or too large")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        data = stream.read(MAX_IMAGE_BYTES + 1)
        after = os.fstat(stream.fileno())
    if len(data) != before.st_size or (before.st_size, before.st_mtime_ns) != (
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError("Screenshot changed during read")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Screenshot must be a PNG")
    chunks: list[bytes] = []
    offset, compressed, header = 8, bytearray(), None
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("Truncated PNG chunk")
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : end - 4]
        if zlib.crc32(kind + payload) != struct.unpack_from(">I", data, end - 4)[0]:
            raise ValueError("Invalid PNG checksum")
        if kind == b"IHDR":
            if length != 13 or header is not None or chunks:
                raise ValueError("Invalid PNG header")
            header = struct.unpack(">IIBBBBB", payload)
        if kind == b"IEND" and length:
            raise ValueError("Invalid PNG end marker")
        if kind == b"IDAT":
            compressed.extend(payload)
        chunks.append(kind)
        offset = end
    if (
        offset != len(data)
        or not header
        or chunks[-1] != b"IEND"
        or chunks.count(b"IEND") != 1
        or not compressed
    ):
        raise ValueError("Incomplete PNG")
    width, height, depth, color, compression, filtering, interlace = header
    depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
    if (
        not width
        or not height
        or depth not in depths.get(color, ())
        or compression
        or filtering
        or interlace not in (0, 1)
    ):
        raise ValueError("Invalid PNG dimensions or pixel format")
    if color == 3 and b"PLTE" not in chunks:
        raise ValueError("Indexed PNG lacks its palette")
    bits = depth * {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    passes = (
        ((0, 0, 1, 1),)
        if not interlace
        else (
            (0, 0, 8, 8),
            (4, 0, 8, 8),
            (0, 4, 4, 8),
            (2, 0, 4, 4),
            (0, 2, 2, 4),
            (1, 0, 2, 2),
            (0, 1, 1, 2),
        )
    )
    scanlines = []
    for x, y, dx, dy in passes:
        columns, rows = max(0, (width - x + dx - 1) // dx), max(0, (height - y + dy - 1) // dy)
        if columns and rows:
            scanlines.append((rows, (columns * bits + 7) // 8 + 1))
    expected = sum(rows * stride for rows, stride in scanlines)
    if expected > MAX_IMAGE_BYTES:
        raise ValueError("Oversized PNG pixels")
    decoder = zlib.decompressobj()
    try:
        decoded = decoder.decompress(compressed, MAX_IMAGE_BYTES + 1)
    except zlib.error as exc:
        raise ValueError("Invalid PNG pixels") from exc
    if len(decoded) != expected or not decoder.eof or decoder.unused_data:
        raise ValueError("Invalid PNG pixel count")
    offset = 0
    for rows, stride in scanlines:
        if any(decoded[offset + row * stride] > 4 for row in range(rows)):
            raise ValueError("Invalid PNG scanline filter")
        offset += rows * stride
    return data


def screenshot_artifacts(evidence: Path, options: dict, arms: list[dict]) -> tuple[list[dict], list[dict]]:
    """Declare recorded screenshots and every available A/B round comparison.

    Args:
        evidence: Owned game evidence directory.
        options: Frozen options containing rounds and optional quality_samples.
        arms: Arm receipts, including the original shot and extra quality shots.

    Returns:
        Artifact declarations with exact hashes, sizes and media types, plus
        game comparisons. Captures from different arms are never same-frame
        ground truth. Unrecorded, missing or invalid images are omitted.
    """
    artifacts, comparisons = [], []
    for pair in range(1, options["rounds"] + 1):
        available: dict[int, list[dict[str, str]]] = {}
        for side in "AB":
            label = f"{side}{pair}"
            records = [arm for arm in arms if arm.get("arm") == label and arm.get("side") == side and arm.get("pair") == pair]
            if len(records) != 1 or records[0].get("in_game") is not True:
                continue
            arm = records[0]
            samples = arm.get("quality_samples", {})
            captures = samples.get("captures", []) if isinstance(samples, dict) else []
            paths = [arm.get("shot"), *(captures if isinstance(captures, list) else [])]
            for index, recorded in enumerate(paths):
                suffix = "" if index == 0 else f"_quality_{index}"
                try:
                    if not isinstance(recorded, str):
                        continue
                    path = evidence / "shots" / Path(recorded).name
                    if (Path(recorded).absolute() != path.absolute()
                            or not re.fullmatch(rf"{label}(?:#r[1-9][0-9]*)?{suffix}\.png", path.name)):
                        continue
                    data = _image(path)
                except (OSError, ValueError, RuntimeError):
                    continue
                role = "baseline" if side == "A" else "candidate"
                name = path.relative_to(evidence).as_posix()
                artifacts.append({
                    "name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "media_type": "image/png",
                    "description": f"{role.title()} ({label}, round {pair}); after timing and before pan; distinct game capture.",
                })
                available.setdefault(index, []).append({"role": role, "artifact": name})
        for index, images in sorted(available.items()):
            if len(images) == 2:
                suffix = "" if index == 0 else f"-quality-{index}"
                comparisons.append({
                    "id": f"game-round-{pair}{suffix}", "label": f"Game round {pair}{suffix}: distinct A/B captures",
                    "kind": "game", "images": images,
                })
    return artifacts, comparisons


def quality_artifacts(evidence: Path) -> list[dict]:
    """Declare summary and per-frame official reports without following aliases."""
    artifacts = []
    media = {
        "iq-judge.json": "application/json",
        "iq-judge.md": "text/markdown",
        "iq-judge.stderr.txt": "text/plain",
        "iq-judge.stdout.txt": "text/plain",
    }
    for path in sorted((Path(evidence) / "quality").rglob("*")):
        if (
            path.name not in media
            or not path.is_file()
            or any(parent.is_symlink() for parent in (path, *path.parents))
        ):
            continue
        data = path.read_bytes()
        if data:
            artifacts.append(
                {
                    "name": path.relative_to(evidence).as_posix(),
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "media_type": media[path.name],
                }
            )
    return artifacts


def _directory(evidence: Path, relative: str) -> Path:
    """Create an owned report directory without traversing symbolic links."""
    output = evidence / relative
    if any(parent.is_symlink() for parent in (output, *output.parents)):
        raise ValueError("Quality report directory contains a symbolic link")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _write_report(path: Path, text: str) -> None:
    """Retain a report without overwriting a linked or externally owned file."""
    if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_nlink != 1)):
        raise ValueError("Quality report file is not exclusively owned")
    path.write_text(text)


def _frame(frame_id: str | int, directory: str) -> dict:
    """Initialize one independent official measurement with no substitute metrics."""
    return {
        "frame_id": frame_id,
        "status": "not_measured",
        "metrics": [],
        "not_computed": [],
        "provenance": {
            "tool": "phonefarm iq-judge",
            "captures": {"baseline": [], "candidate": []},
            "json_artifact": f"{directory}/iq-judge.json",
            "markdown_artifact": f"{directory}/iq-judge.md",
        },
    }


def _asset(evidence: Path, item: object, require_size: bool) -> tuple[dict, tuple[int, int]]:
    """Verify a manifest entry's owned relative PNG, checksum and readback size."""
    if not isinstance(item, dict):
        raise ValueError("Image manifest entry must be an object")
    name, digest = item.get("artifact"), item.get("sha256")
    if (
        not isinstance(name, str)
        or not name
        or "," in name
        or "\\" in name
        or "\x00" in name
        or Path(name).is_absolute()
        or ".." in Path(name).parts
        or Path(name).as_posix() != name
        or Path(name).suffix.lower() != ".png"
    ):
        raise ValueError(
            "Image artifact must be a canonical relative PNG path owned by this evidence"
        )
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Image artifact lacks a valid SHA256")
    path = evidence / name
    data = _image(path)
    identity = path.stat()
    if identity.st_nlink != 1:
        raise ValueError("Image artifact is a hard link, not an independent owned readback")
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Image SHA256 does not match its manifest")
    if (require_size or "size" in item) and (
        type(item.get("size")) is not int or item["size"] != len(data)
    ):
        raise ValueError("Image size does not match its manifest")
    return {"artifact": name, "sha256": digest, "size": len(data)}, (
        identity.st_dev,
        identity.st_ino,
    )


def _claim(
    frame: dict, asset: dict, identity: tuple[int, int], owners: dict[str | tuple[int, int], dict]
) -> None:
    """Reject reused source/readback paths or file identities before running any judge."""
    for key in (asset["artifact"], identity):
        previous = owners.get(key)
        if previous is not None:
            reason = f"Image artifact is reused across roles or frames: {asset['artifact']}"
            frame["not_computed"].append(reason)
            previous["not_computed"].append(reason)
        else:
            owners[key] = frame


def _finite(value: object) -> bool:
    """Exclude booleans, nonfinite floats and integers outside finite float range."""
    try:
        return (type(value) is int or type(value) is float) and math.isfinite(value)
    except OverflowError:
        return False


def _float(value: str) -> float:
    """Reject overflowing JSON numbers rather than retaining infinite metrics."""
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite official IQ number")
    return number


def _judge_owned(evidence: Path, frame: dict, phonefarm: str) -> None:
    """Run and retain official IQ for exactly one validated source or legacy pair."""
    provenance = frame["provenance"]
    json_path = evidence / provenance["json_artifact"]
    output = _directory(evidence, json_path.parent.relative_to(evidence).as_posix())
    raw = None
    if not frame["not_computed"]:
        captures = provenance["captures"]
        command = [
            phonefarm,
            "iq-judge",
            "--ctrl",
            ",".join(str(evidence / item["artifact"]) for item in captures["baseline"]),
            "--knob",
            ",".join(str(evidence / item["artifact"]) for item in captures["candidate"]),
            "--sheet",
            str(output / "iq-judge.md"),
        ]
        provenance["command"] = command
        try:
            # Check report ownership before allowing the official tool to write its sheet.
            for name in (
                "iq-judge.json",
                "iq-judge.md",
                "iq-judge.stdout.txt",
                "iq-judge.stderr.txt",
            ):
                path = output / name
                if path.is_symlink() or (
                    path.exists() and (not path.is_file() or path.stat().st_nlink != 1)
                ):
                    raise ValueError("Official IQ report path is not exclusively owned")
            process = subprocess.run(command, capture_output=True, text=True, timeout=300)
            _write_report(output / "iq-judge.stdout.txt", process.stdout)
            _write_report(output / "iq-judge.stderr.txt", process.stderr)
            provenance["exit_code"] = process.returncode
            if process.returncode != 0:
                raise ValueError(f"phonefarm iq-judge exited with {process.returncode}")
            raw = json.loads(process.stdout, parse_constant=_reject_constant, parse_float=_float)
            if (
                not isinstance(raw, dict)
                or not isinstance(raw.get("metrics"), list)
                or not isinstance(raw.get("sheet"), str)
            ):
                raise ValueError("Official IQ output lacks metric rows or Markdown sheet")
            reasons = raw.get("not_computed")
            if not isinstance(reasons, list) or any(
                not isinstance(reason, str) or not reason.strip() for reason in reasons
            ):
                raise ValueError("Official IQ output lacks explicit missing-measurement reasons")
            frame["metrics"] = raw["metrics"]
            frame["not_computed"].extend(reasons)
            for key, direction in IQ_KEYS.items():
                rows = [
                    row for row in raw["metrics"] if isinstance(row, dict) and row.get("key") == key
                ]
                row = rows[0] if len(rows) == 1 else {}
                valid = all(
                    _finite(row.get(field)) for field in ("value", "native_self", "ref_line")
                )
                if row.get("better") != direction or not valid:
                    frame["not_computed"].append(
                        f"{key}: official IQ measurement or self-noise reference unavailable"
                    )
            frame["status"] = "not_measured" if frame["not_computed"] else "measured"
            _write_report(output / "iq-judge.md", raw["sheet"])
        except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
            frame["status"] = "error"
            frame["not_computed"].append(f"Official IQ failed: {exc}")
    if frame["status"] == "error" or raw is None:
        _write_report(
            output / "iq-judge.md",
            "# Game IQ unavailable\n\n" + "\n".join(frame["not_computed"]) + "\n",
        )
    _write_report(
        json_path, json.dumps(raw if raw is not None else frame, indent=2, allow_nan=False)
    )


def _judge(evidence: Path, frame: dict, phonefarm: str) -> None:
    """Keep report-directory and file failures explicit without losing other frames."""
    try:
        _judge_owned(evidence, frame, phonefarm)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        frame["status"] = "error"
        frame["not_computed"].append(f"Official IQ report unavailable: {exc}")


def _finish(
    evidence: Path,
    frames: list[dict],
    comparison: str,
    missing: list[str] | None = None,
    protocol: str | None = None,
) -> dict:
    """Summarize independent reports without averaging raw reference lines or measurements."""
    reasons = list(missing or [])
    reasons.extend(
        f"{frame['frame_id']}: {reason}" for frame in frames for reason in frame["not_computed"]
    )
    status = (
        "error"
        if any(frame["status"] == "error" for frame in frames)
        else (
            "measured"
            if frames and not reasons and all(frame["status"] == "measured" for frame in frames)
            else "not_measured"
        )
    )
    provenance = {
        "tool": "phonefarm iq-judge",
        "comparison": comparison,
        "aggregation": (
            "independent official best candidate per metric for each frame; "
            "average per-frame quality penalties, never raw rows"
        ),
        "captures": {
            role: [
                dict(item, frame_id=frame["frame_id"])
                for frame in frames
                for item in frame["provenance"]["captures"][role]
            ]
            for role in ("baseline", "candidate")
        },
        "frame_reports": [
            {
                "frame_id": frame["frame_id"],
                "status": frame["status"],
                "json_artifact": frame["provenance"]["json_artifact"],
                "markdown_artifact": frame["provenance"]["markdown_artifact"],
            }
            for frame in frames
        ],
        "json_artifact": "quality/iq-judge.json",
        "markdown_artifact": "quality/iq-judge.md",
    }
    result = {
        "status": status,
        "metrics": [],
        "frames": frames,
        "required_frames": len(frames),
        "not_computed": reasons,
        "provenance": provenance,
    }
    if protocol is not None:
        result["protocol"] = provenance["protocol"] = protocol
    if not frames and not reasons:
        reasons.append("No quality frames were provided")
    title = (
        "Game IQ per-frame reports" if status == "measured" else "Game IQ unavailable or incomplete"
    )
    lines = [f"# {title}", "", comparison, "", *reasons]
    lines.extend(
        f"- {frame['frame_id']}: {frame['status']} — {frame['provenance']['markdown_artifact']}"
        for frame in frames
    )
    try:
        output = _directory(evidence, "quality")
        _write_report(output / "iq-judge.md", "\n".join(lines) + "\n")
        _write_report(output / "iq-judge.json", json.dumps(result, indent=2, allow_nan=False))
    except (OSError, ValueError) as exc:
        result["status"] = "error"
        reasons.append(f"Quality summary unavailable: {exc}")
    return result


def judge_replay(evidence: Path, frames: list[dict], phonefarm: str) -> dict:
    """Judge independent GPU readbacks of each captured input without cross-frame pooling.

    Args:
        evidence: Owned evidence root containing every source and readback PNG.
        frames: Entries with a unique frame_id, checksum-bound source, and at least
            three independently owned baseline and candidate artifact/sha256/size
            entries. Identical pixel bytes are valid; reused paths and inodes are not.
        phonefarm: Official executable; only local-file iq-judge commands are invoked.

    Returns:
        Strict overall status and per-frame status, metrics, not_computed and
        provenance in frames. Overall metrics stays empty so consumers average
        per-frame quality penalties rather than raw measurements or references.
    """
    evidence = Path(evidence).absolute()
    measured: list[dict] = []
    owners: dict[str | tuple[int, int], dict] = {}
    identifiers: dict[str, dict] = {}
    missing: list[str] = []
    if not isinstance(frames, list):
        missing.append("Replay frames must be a list")
        frames = []
    for index, entry in enumerate(frames, 1):
        entry = entry if isinstance(entry, dict) else {}
        identifier = entry.get("frame_id")
        frame_id: str | int | None = None
        if (type(identifier) is int and identifier >= 0) or (
            isinstance(identifier, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", identifier)
        ):
            frame_id = identifier
        frame = _frame(frame_id if frame_id is not None else f"invalid-{index}", f"quality/frames/{index:04d}")
        frame["provenance"]["protocol"] = REPLAY_PROTOCOL
        measured.append(frame)
        recorded_reasons = entry.get("not_computed", [])
        if isinstance(recorded_reasons, list):
            frame["not_computed"].extend(
                reason for reason in recorded_reasons if isinstance(reason, str) and reason.strip()
            )
        if isinstance(entry.get("error"), str) and entry["error"].strip():
            frame["not_computed"].append(entry["error"])
        if "cleanup_confirmed" in entry and entry["cleanup_confirmed"] is not True:
            frame["not_computed"].append("Replay cleanup was not confirmed")
        if frame_id is None:
            frame["not_computed"].append(
                "Replay frame_id must be a unique safe name or nonnegative integer"
            )
        elif str(identifier) in identifiers:
            reason = f"Duplicate replay frame_id: {identifier}"
            frame["not_computed"].append(reason)
            identifiers[str(identifier)]["not_computed"].append(reason)
        else:
            identifiers[str(identifier)] = frame
        try:
            source, identity = _asset(evidence, entry.get("source"), False)
            frame["provenance"]["source"] = source
            _claim(frame, source, identity, owners)
        except OSError as exc:
            frame["status"] = "error"
            frame["not_computed"].append(f"source: {exc}")
        except (ValueError, RuntimeError) as exc:
            frame["not_computed"].append(f"source: {exc}")
        for role in ("baseline", "candidate"):
            samples = entry.get(role)
            if not isinstance(samples, list) or len(samples) < 3:
                frame["not_computed"].append(
                    f"{role}: at least three independent GPU readback PNGs are required"
                )
            for sample in samples if isinstance(samples, list) else []:
                try:
                    asset, identity = _asset(evidence, sample, True)
                    if "frame_id" in sample and sample["frame_id"] != identifier:
                        raise ValueError("Readback belongs to a different frame_id")
                    source_hash = frame["provenance"].get("source", {}).get("sha256")
                    if "source_sha256" in sample and sample["source_sha256"] != source_hash:
                        raise ValueError("Readback belongs to a different captured input")
                    frame["provenance"]["captures"][role].append(asset)
                    _claim(frame, asset, identity, owners)
                except OSError as exc:
                    frame["status"] = "error"
                    frame["not_computed"].append(f"{role}: {exc}")
                except (ValueError, RuntimeError) as exc:
                    frame["not_computed"].append(f"{role}: {exc}")
    for frame in measured:
        _judge(evidence, frame, phonefarm)
    return _finish(
        evidence,
        measured,
        "Independent baseline/candidate GPU readbacks of each checksum-bound same captured input",
        missing,
        REPLAY_PROTOCOL,
    )


def measure_quality(evidence: Path, options: dict, arms: list[dict], phonefarm: str) -> dict:
    """Measure each legacy A/B pair separately without pooling different cold starts.

    Args:
        evidence: Owned evidence directory with post-window game captures.
        options: Frozen rounds and positive quality_samples count.
        arms: Completed paired game arms and acquisition receipts.
        phonefarm: Official executable; iq-judge reads only the selected local pair.

    Returns:
        Strict overall status, per-pair original official rows in frames, and
        combined checksum-bound provenance. Legacy captures remain distinct
        game screenshots, never same-input GPU replay or synchronized truth.
    """
    evidence = Path(evidence).absolute()
    artifacts, _ = screenshot_artifacts(evidence, options, arms)
    by_name = {item["name"]: item for item in artifacts}
    frames: list[dict] = []
    owners: dict[str | tuple[int, int], dict] = {}
    for pair in range(1, options["rounds"] + 1):
        frame = _frame(f"round-{pair}", f"quality/rounds/{pair:04d}")
        frame["pair"] = pair
        frames.append(frame)
        for side, role in (("A", "baseline"), ("B", "candidate")):
            label = f"{side}{pair}"
            records = [
                arm
                for arm in arms
                if arm.get("arm") == label and arm.get("side") == side and arm.get("pair") == pair
            ]
            arm = records[0] if len(records) == 1 else {}
            samples = arm.get("quality_samples", {})
            if (
                len(records) != 1
                or not isinstance(samples, dict)
                or samples.get("error")
                or arm.get("error")
            ):
                frame["not_computed"].append(
                    f"{label}: quality acquisition failed or arm is invalid"
                )
            paths = samples.get("captures", []) if isinstance(samples, dict) else []
            paths = paths if isinstance(paths, list) else []
            for index in range(1, options["quality_samples"] + 1):
                recorded = paths[index - 1] if index <= len(paths) else None
                pattern = rf"{label}(?:#r[1-9][0-9]*)?_quality_{index}\.png"
                if not isinstance(recorded, str) or not re.fullmatch(pattern, Path(recorded).name):
                    frame["not_computed"].append(f"{label}: missing real capture {index}")
                    continue
                name = f"shots/{Path(recorded).name}"
                artifact = by_name.get(name)
                try:
                    asset, identity = _asset(evidence, {"artifact": name, **(artifact or {})}, True)
                    if artifact is None:
                        raise ValueError("Capture is not owned by this A/B arm")
                    frame["provenance"]["captures"][role].append(asset)
                    _claim(frame, asset, identity, owners)
                except (OSError, ValueError, RuntimeError) as exc:
                    frame["not_computed"].append(f"{label}: {exc}")
            if len(frame["provenance"]["captures"][role]) < 3:
                frame["not_computed"].append(
                    f"{label}: Official game IQ requires at least three real baseline and candidate captures per pair"
                )
    for frame in frames:
        _judge(evidence, frame, phonefarm)
    return _finish(
        evidence,
        frames,
        "Distinct real-game A/B captures, judged separately per pair; not same-frame truth",
    )
