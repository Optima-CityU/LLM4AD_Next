#!/usr/bin/env python3
"""Strict offline raw capture validation and phonefarm PNG bridge.

File identity and declared pairing only. No GPU/source attestation, performance
statistics, or proof of real-game optimization. See https://github.com/HGamey/docs/blob/main/repos/refbench/docs/SPEC_NATIVE_PAIRED_CAPTURE.md
"""
import argparse
import binascii
import hashlib
import json
import math
import os
import stat
from pathlib import Path
import re
import struct
import sys
import zlib

SCHEMA = "refbench/native_paired_capture@1"
SOURCE_NATIVE = "native_rendered_controlled_whitebox"
SOURCE_FIXTURE = "synthetic_fixture"
MAX_JSON = 1024 * 1024
MAX_RAW = 128 * 1024 * 1024
MAX_ARTIFACT = 64 * 1024 * 1024
MAX_CAPTURE_RAW = 384 * 1024 * 1024
TIMESTAMP_SCOPE = "CLOCK_MONOTONIC_before_command_recording_not_gpu_or_present"
TOP = set("schema run_id scene scene_content source capture_mode performance_eligible real_game_source_attested framegen_intermediate_truth timestamp_scope capture_start_unix_ms capture_end_unix_ms rules_frozen_at_unix_ms rules_sha256 scene_shader_sha256 vertex_shader_sha256 candidate_sha256 total_frames intensity start count stride failure_reason complete frames".split())
FRAME = set("frame_index host_frame_id timestamp_ns phase phase_bits iters camera exposure images".split())
IMAGE = set("role file width height row_bytes bytes sha256 vk_format color_encoding source".split())
THRESHOLDS = set("min_frames max_pair_timestamp_delta_ns max_frame_gap_ns max_alignment_error_px min_psnr_db max_channel_bias max_temporal_residual_mae min_reference_motion_mae max_reference_motion_mae".split())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def regular_bytes(path, limit):
    path = Path(path)
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise ValueError(f"not a regular non-symlink file: {path}: {error}") from error
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        require(stat.S_ISREG(metadata.st_mode), f"not a regular non-symlink file: {path}")
        require(metadata.st_size <= limit, f"file exceeds bound: {path}")
        result = stream.read(limit + 1)
    require(len(result) <= limit, f"file grew beyond bound: {path}")
    return result


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        require(key not in obj, f"duplicate JSON field: {key}")
        obj[key] = value
    return obj


def parse(data):
    return json.loads(data, object_pairs_hook=unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"nonfinite JSON: {value}")))


def keys(obj, expected, context):
    require(isinstance(obj, dict) and set(obj) == expected, f"{context}: missing or unknown fields")


def integer(value, lo, hi, label):
    require(type(value) is int and lo <= value <= hi, f"{label}: invalid integer")


def hash_value(value, label):
    require(isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value), f"{label}: invalid sha256")


def phase_bits(frame, total):
    return struct.unpack("<I", struct.pack("<f", 2.0 * math.pi * frame / total))[0]


def frozen_rules(data):
    rules = parse(data)
    keys(rules, {"schema", "protocol_id", "frozen_at_unix_ms", "thresholds", "exclude_rects"}, "rules")
    require(rules["schema"] == "phonefarm/sequence_quality_rules@1", "rules schema")
    require(isinstance(rules["protocol_id"], str) and rules["protocol_id"].strip(), "rules protocol_id")
    integer(rules["frozen_at_unix_ms"], 1, 2**64 - 1, "rules frozen_at")
    keys(rules["thresholds"], THRESHOLDS, "rules thresholds")
    t = rules["thresholds"]
    for name in ("min_frames", "max_pair_timestamp_delta_ns", "max_frame_gap_ns"):
        integer(t[name], 2 if name == "min_frames" else 0, 2**64 - 1, name)
    for name in THRESHOLDS - {"min_frames", "max_pair_timestamp_delta_ns", "max_frame_gap_ns"}:
        require(type(t[name]) in (int, float) and math.isfinite(t[name]) and t[name] >= 0, f"threshold {name}")
    require(t["max_frame_gap_ns"] > 0 and t["min_reference_motion_mae"] < t["max_reference_motion_mae"], "invalid temporal thresholds")
    require(isinstance(rules["exclude_rects"], list), "exclude_rects")
    for rect in rules["exclude_rects"]:
        require(isinstance(rect, list) and len(rect) == 4 and all(type(x) in (int, float) and math.isfinite(x) and 0 <= x <= 1 for x in rect), "exclude rectangle")
        require(rect[0] < rect[2] and rect[1] < rect[3], "empty exclude rectangle")
    return rules


def load_capture(manifest_path, rules_path, candidate_path=None):
    """Return immutable checked byte snapshots; hashes are not acquisition attestation."""
    manifest_path = Path(manifest_path)
    manifest_bytes = regular_bytes(manifest_path, MAX_JSON)
    m = parse(manifest_bytes)
    keys(m, TOP, "capture")
    require(m["schema"] == SCHEMA, "capture schema")
    require(isinstance(m["run_id"], str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", m["run_id"]) and m["run_id"] not in (".", "..", "unset"), "run id")
    require(m["source"] in (SOURCE_NATIVE, SOURCE_FIXTURE), "unrecognized capture source")
    require(m["scene"] == "sr_pipeline" and m["scene_content"] == ("procedural_alu" if m["source"] == SOURCE_NATIVE else "synthetic_fixture"), "scene identity")
    require(m["capture_mode"] == "quality_only" and m["performance_eligible"] is False and m["real_game_source_attested"] is False and m["framegen_intermediate_truth"] is False, "capture claim scope")
    require(m["timestamp_scope"] == TIMESTAMP_SCOPE, "timestamp scope")
    require(m["complete"] is True, "incomplete capture")
    require(m["failure_reason"] == "", "failed capture cannot be complete")
    for name, lo, hi in (("total_frames", 1, 100000), ("intensity", 1, 64), ("start", 0, 100000), ("count", 1, 16), ("stride", 1, 16), ("capture_start_unix_ms", 1, 2**64-1), ("capture_end_unix_ms", 1, 2**64-1), ("rules_frozen_at_unix_ms", 1, 2**64-1)):
        integer(m[name], lo, hi, name)
    require(m["start"] + (m["count"] - 1) * m["stride"] < m["total_frames"], "capture schedule outside run")
    require(m["capture_start_unix_ms"] > m["rules_frozen_at_unix_ms"] and m["capture_end_unix_ms"] >= m["capture_start_unix_ms"], "rules not frozen before capture or reversed clock")
    for name in ("rules_sha256", "scene_shader_sha256", "vertex_shader_sha256"):
        hash_value(m[name], name)
    require(isinstance(m["candidate_sha256"], str), "candidate sha type")
    rules_bytes = regular_bytes(rules_path, MAX_JSON)
    rules = frozen_rules(rules_bytes)
    require(sha(rules_bytes) == m["rules_sha256"] and rules["frozen_at_unix_ms"] == m["rules_frozen_at_unix_ms"], "frozen rules binding mismatch")
    candidate_bytes = None
    if m["candidate_sha256"]:
        hash_value(m["candidate_sha256"], "candidate")
        require(candidate_path is not None, "capture candidate requires --candidate-shader current artifact")
        candidate_bytes = regular_bytes(candidate_path, MAX_ARTIFACT)
        require(sha(candidate_bytes) == m["candidate_sha256"], "candidate artifact binding mismatch")
    else:
        require(candidate_path is None, "no candidate artifact was executed in capture")
    wanted = ["native_lr", "native_hr"] + (["postfx_candidate"] if candidate_bytes is not None else [])
    require(isinstance(m["frames"], list) and len(m["frames"]) == m["count"], "frame count mismatch")
    blobs = {}
    last_timestamp = 0
    hr_size = None
    capture_raw_bytes = 0
    for ordinal, frame in enumerate(m["frames"]):
        keys(frame, FRAME, "frame")
        expected = m["start"] + ordinal * m["stride"]
        require(type(frame["frame_index"]) is int and frame["frame_index"] == expected and frame["host_frame_id"] == f'{m["run_id"]}:{expected}', "frame index/identity mismatch")
        integer(frame["timestamp_ns"], last_timestamp + 1, 2**64-1, "timestamp")
        last_timestamp = frame["timestamp_ns"]
        bits = phase_bits(expected, m["total_frames"])
        integer(frame["phase_bits"], 0, 2**32-1, "phase bits")
        require(type(frame["phase"]) in (float, int) and math.isfinite(frame["phase"]), "phase type")
        require(frame["phase_bits"] == bits and struct.unpack("<I", struct.pack("<f", frame["phase"]))[0] == bits, "phase mismatch")
        require(type(frame["iters"]) is int and frame["iters"] == m["intensity"] * 64 and frame["camera"] == "fullscreen_uv_identity" and type(frame["exposure"]) in (float, int) and frame["exposure"] == 1, "scene conditions mismatch")
        require(isinstance(frame["images"], list) and len(frame["images"]) == len(wanted), "image roles missing/duplicate")
        sizes = {}
        for role, image in zip(wanted, frame["images"]):
            keys(image, IMAGE, "image")
            require(image["role"] == role and image["source"] == m["source"], "native/candidate role or source mismatch")
            integer(image["width"], 1, 8192, "width")
            integer(image["height"], 1, 8192, "height")
            w, h = image["width"], image["height"]
            require(w*h <= 32000000, "pixel bound")
            if m["source"] == SOURCE_NATIVE:
                require((w, h) == ((1280, 720) if role == "native_lr" else (1920, 1080)), "native dimensions mismatch")
            require(type(image["bytes"]) is int and image["bytes"] == w*h*4 and type(image["row_bytes"]) is int and image["row_bytes"] == w*4, "tight RGBA byte count")
            capture_raw_bytes += image["bytes"]
            require(capture_raw_bytes <= MAX_CAPTURE_RAW, "total raw capture exceeds host snapshot bound")
            require(image["vk_format"] == "R8G8B8A8_UNORM" and image["color_encoding"] == "linear_shader_output", "unsupported raw/color format")
            require(image["file"] == f"{expected}_{role}.rgba", "unexpected raw file name/path")
            hash_value(image["sha256"], "raw image")
            data = regular_bytes(manifest_path.parent / image["file"], MAX_RAW)
            require(len(data) == image["bytes"] and sha(data) == image["sha256"], "raw image bytes/hash mismatch")
            blobs[image["file"]] = data
            sizes[role] = (w, h)
        require(sizes["native_lr"][0] < sizes["native_hr"][0] and sizes["native_lr"][1] < sizes["native_hr"][1], "native LR must have lower raster dimensions")
        require(sizes["native_lr"][0]*sizes["native_hr"][1] == sizes["native_hr"][0]*sizes["native_lr"][1], "LR/HR aspect mismatch")
        require(hr_size is None or sizes["native_hr"] == hr_size, "sequence dimensions changed")
        hr_size = sizes["native_hr"]
        if candidate_bytes is not None:
            require(sizes["postfx_candidate"] == hr_size, "candidate is not HR-sized")
    # A complete manifest alone is a checkpoint, not a published capture.
    # Keep old captures unchanged: no marker means explicit rejection.
    receipt_bytes = regular_bytes(manifest_path.parent / "completion.json", MAX_JSON)
    receipt = parse(receipt_bytes)
    keys(receipt, {"schema", "run_id", "manifest_sha256", "run_output_sha256", "frame_count", "frames_submitted", "total_frames", "complete", "capture_mode", "performance_eligible"}, "completion")
    require(receipt["schema"] == "refbench/native_capture_completion@1" and receipt["run_id"] == m["run_id"], "completion run/schema mismatch")
    require(receipt["complete"] is True and receipt["capture_mode"] == "quality_only" and receipt["performance_eligible"] is False, "completion claim scope")
    for name, expected in (("frame_count", m["count"]), ("frames_submitted", m["total_frames"]), ("total_frames", m["total_frames"])):
        require(type(receipt[name]) is int and receipt[name] == expected, "completion frame count mismatch")
    hash_value(receipt["manifest_sha256"], "completion manifest")
    hash_value(receipt["run_output_sha256"], "completion output")
    require(receipt["manifest_sha256"] == sha(manifest_bytes), "completion manifest binding mismatch")
    output_bytes = regular_bytes(manifest_path.parent / "refbench_out.json", MAX_JSON)
    require(receipt["run_output_sha256"] == sha(output_bytes), "completion run output binding mismatch")
    output = parse(output_bytes)
    require(isinstance(output, dict) and output.get("run_id") == m["run_id"] and output.get("scene") == "sr_pipeline", "run output identity mismatch")
    require(output.get("clean_exit") is True and type(output.get("frames_submitted")) is int and output["frames_submitted"] == m["total_frames"], "run output incomplete")
    require(output.get("capture_mode") == "quality_only" and output.get("performance_eligible") is False, "run output claim scope")
    params = output.get("params")
    require(isinstance(params, dict) and type(params.get("frames")) is int and params["frames"] == m["total_frames"] and type(params.get("intensity")) is int and params["intensity"] == m["intensity"], "run output configuration mismatch")
    require(output.get("effective_knobs") == {"postfx": "on" if candidate_bytes is not None else "off"} and isinstance(output.get("postfx"), dict) and output["postfx"].get("active") is (candidate_bytes is not None), "run output candidate configuration mismatch")
    return m, manifest_bytes, rules, rules_bytes, candidate_bytes, blobs, receipt_bytes, output_bytes


def chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload) & 0xffffffff)


SRGB_LUT = bytes(round(255 * (12.92*(x/255) if x/255 <= 0.0031308 else 1.055*(x/255)**(1/2.4)-0.055)) for x in range(256))


def png(raw, width, height):
    require(all(alpha == 255 for alpha in raw[3::4]), "transparent candidate/raw: no alpha-compositing contract")
    rgb = bytearray(width*height*3)
    rgb[0::3] = raw[0::4].translate(SRGB_LUT)
    rgb[1::3] = raw[1::4].translate(SRGB_LUT)
    rgb[2::3] = raw[2::4].translate(SRGB_LUT)
    scan = b"".join(b"\0" + rgb[y*width*3:(y+1)*width*3] for y in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"sRGB", b"\0") + chunk(b"IDAT", zlib.compress(scan)) + chunk(b"IEND", b"")


def json_bytes(obj):
    return (json.dumps(obj, indent=2, allow_nan=False) + "\n").encode()


def write_new(path, data):
    with Path(path).open("xb") as stream:
        stream.write(data)
        stream.flush()
        import os
        os.fsync(stream.fileno())


def import_capture(manifest_path, rules_path, candidate_path, out):
    m, manifest_bytes, rules, rules_bytes, candidate_bytes, blobs, completion_bytes, output_bytes = load_capture(manifest_path, rules_path, candidate_path)
    require(candidate_bytes is not None, "PNG comparison requires an executed postfx_candidate; native pairs alone are valid capture assets")
    # Validate/convert all snapshots before output exists; never mutate source inputs.
    converted = {}
    for frame in m["frames"]:
        for image in frame["images"]:
            converted[image["file"]] = png(blobs[image["file"]], image["width"], image["height"])
            require(len(converted[image["file"]]) <= MAX_ARTIFACT, "PNG exceeds consumer file bound")
    out = Path(out)
    out.mkdir(mode=0o700)  # exclusive, no overwrite/resume inferred from path
    (out / "raw").mkdir()
    write_new(out / "capture_manifest.json", manifest_bytes)
    # Exact snapshots checked by load_capture; never reopen after validation.
    write_new(out / "completion.json", completion_bytes)
    write_new(out / "refbench_out.json", output_bytes)
    write_new(out / "frozen_rules.json", rules_bytes)
    write_new(out / "candidate.spv", candidate_bytes)
    sequence = m["run_id"]
    timeline = f'{sequence}:record_frame'
    provenance = f"capture_manifest_sha256={sha(manifest_bytes)}; declared source={m['source']}; content={m['scene_content']}; not GPU/acquisition attested"
    derivation = "raw linear UNORM8 to sRGB8 via IEC 61966-2-1 transfer; " + ("declared independent native HR raster, no HR downsampling" if m["source"] == SOURCE_NATIVE else "CPU fixture, no native GPU raster claim") + "; no real-game attestation"
    source = {"kind": "unknown" if m["source"] == SOURCE_NATIVE else "synthetic_fixture", "provenance": provenance, "capture_method": "same_frame_dual_output", "derivation": derivation}
    base = {"schema": "phonefarm/paired_frame_sequence@1", "protocol_id": rules["protocol_id"], "rules_sha256": m["rules_sha256"], "sequence_id": sequence, "timeline_id": timeline, "captured_at_unix_ms": m["capture_start_unix_ms"], "color_space": "sRGB8", "content_scope": "scene_only", "source": source, "candidate_artifact": {"file": "candidate.spv", "sha256": m["candidate_sha256"]}}
    reference = dict(base, role="reference", frames=[])
    candidate = dict(base, role="candidate", frames=[])
    ledger = {"schema": "phonefarm/frame_pair_ledger@1", "sequence_id": sequence, "timeline_id": timeline, "capture_method": "same_frame_dual_output", "provenance": provenance, "candidate_artifact_sha256": m["candidate_sha256"], "pairs": []}
    sidecar = {"schema": "refbench/native_pair_import@1", "complete": True, "capture_manifest_sha256": sha(manifest_bytes), "rules_sha256": m["rules_sha256"], "candidate_sha256": m["candidate_sha256"], "declared_source": m["source"], "source_attestation": "not_checked", "performance_eligible": False, "real_game_qualification": False, "no_ghosting_proven": False, "color_derivation": "linear UNORM8 to sRGB8; round to nearest; alpha must be opaque", "frames": []}
    for frame in m["frames"]:
        converted_images = []
        role_frames = {}
        for image in frame["images"]:
            rawname = image["file"]
            pngname = rawname.removesuffix(".rgba") + ".png"
            write_new(out / "raw" / rawname, blobs[rawname])
            write_new(out / pngname, converted[rawname])
            pnghash = sha(converted[rawname])
            converted_images.append(dict(image, raw_file="raw/" + rawname, png_file=pngname, png_sha256=pnghash))
            role_frames[image["role"]] = {"frame_id": frame["frame_index"], "timestamp_ns": frame["timestamp_ns"], "file": pngname, "sha256": pnghash, "width": image["width"], "height": image["height"], "alignment": {"method": "exact_pair", "reference_frame_id": frame["frame_index"], "error_px": 0.0, "evidence": f"declared same record_frame phase_bits={frame['phase_bits']}; {provenance}"}}
        ref, cand = role_frames["native_hr"], role_frames["postfx_candidate"]
        reference["frames"].append(ref)
        candidate["frames"].append(cand)
        ledger["pairs"].append({"frame_id": frame["frame_index"], "timestamp_ns": frame["timestamp_ns"], "candidate_timestamp_ns": frame["timestamp_ns"], "reference_sha256": ref["sha256"], "candidate_sha256": cand["sha256"]})
        sidecar["frames"].append(dict(frame, images=converted_images))
    ledger_bytes = json_bytes(ledger)
    write_new(out / "pair_ledger.json", ledger_bytes)
    for obj in (reference, candidate):
        obj["pairing_evidence"] = {"file": "pair_ledger.json", "sha256": sha(ledger_bytes)}
    write_new(out / "reference.json", json_bytes(reference))
    write_new(out / "candidate.json", json_bytes(candidate))
    # This marker is last: partial IO cannot appear as a complete import.
    write_new(out / "import_provenance.json", json_bytes(sidecar))
    return {"schema": "refbench/native_pair_import_summary@1", "out": str(out), "frames": len(m["frames"]), "source": source["kind"], "claim_scope": "declared_pairing_and_file_integrity_only", "real_game_qualification": False, "performance_eligible": False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("validate", "import"))
    p.add_argument("--manifest", required=True)
    p.add_argument("--rules", required=True)
    p.add_argument("--candidate-shader")
    p.add_argument("--out")
    args = p.parse_args(argv)
    try:
        if args.command == "import":
            require(args.out is not None, "import requires --out")
            result = import_capture(args.manifest, args.rules, args.candidate_shader, args.out)
        else:
            m, *_ = load_capture(args.manifest, args.rules, args.candidate_shader)
            result = {"schema": "refbench/native_pair_validation@1", "ledger_consistent": True, "frames": len(m["frames"]), "declared_source": m["source"], "source_attestation": "not_checked", "performance_eligible": False, "real_game_qualification": False}
        print(json.dumps(result, allow_nan=False))
        return 0
    except (ValueError, OSError, OverflowError, struct.error) as exc:
        print(json.dumps({"error": str(exc), "real_game_qualification": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
