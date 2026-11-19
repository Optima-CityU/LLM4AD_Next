#!/usr/bin/env python3
"""Generate tiny synthetic fixtures, never real-game acceptance evidence.

python3 examples/sequence_quality_fixture.py --out tasks/quality-demo --mode ghost
src/target/debug/phonefarm sequence-quality --reference tasks/quality-demo/reference.json \
  --candidate tasks/quality-demo/candidate.json --rules tasks/quality-demo/rules.json \
  --out tasks/quality-demo/report.json
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib


def png(path, w, h, pixels):
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
    rows = b"".join(b"\0" + pixels[y * w * 3:(y + 1) * w * 3] for y in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--mode", choices=["identical", "bias", "flicker", "ghost"], default="identical")
    args = ap.parse_args()
    root = args.out
    if root.exists():
        raise SystemExit("Output already exists; use a new tasks/<name> directory to preserve evidence")
    root.mkdir(parents=True)
    rules = {"schema": "phonefarm/sequence_quality_rules@1", "protocol_id": "synthetic-demo-v1",
             "frozen_at_unix_ms": 100, "thresholds": {
                 "min_frames": 4, "max_pair_timestamp_delta_ns": 1000000, "max_frame_gap_ns": 50000000,
                 "max_alignment_error_px": 0.25, "min_psnr_db": 30.0, "max_channel_bias": 3.0,
                 "max_temporal_residual_mae": 0.25, "min_reference_motion_mae": 0.25,
                 "max_reference_motion_mae": 32.0}, "exclude_rects": []}
    save(root / "rules.json", rules)
    (root / "candidate.fixture").write_text(f"synthetic demo generator mode={args.mode}; not a deployable game shader\n")
    artifact = {"file": "candidate.fixture", "sha256": digest(root / "candidate.fixture")}
    frames = {"reference": [], "candidate": []}
    pairs = []
    previous = None
    w, h = 32, 18
    for t in range(4):
        ref = bytearray(v for y in range(h) for x in range(w) for v in [100 if (x+t) % w < w//2 else 80] * 3)
        cand = ref.copy()
        if args.mode == "bias":
            for i in range(0, len(cand), 3):
                cand[i] += 12
        elif args.mode == "flicker" and t % 2:
            cand = bytearray(v+2 for v in cand)
        elif args.mode == "ghost" and previous is not None:
            cand = bytearray((v+p)//2 for v, p in zip(cand, previous))
        previous = ref
        for role, pixels in [("reference", ref), ("candidate", cand)]:
            name = f"{role}_{t}.png"
            png(root / name, w, h, pixels)
            frames[role].append({"frame_id": t, "timestamp_ns": t*16666667, "file": name,
                                 "sha256": digest(root / name), "width": w, "height": h,
                                 "alignment": {"method": "exact_pair", "reference_frame_id": t, "error_px": 0.0,
                                               "evidence": "program-generated fixture; not trusted capture"}})
        pairs.append({"frame_id": t, "timestamp_ns": t*16666667, "candidate_timestamp_ns": t*16666667,
                      "reference_sha256": frames["reference"][-1]["sha256"],
                      "candidate_sha256": frames["candidate"][-1]["sha256"]})
    ledger = {"schema": "phonefarm/frame_pair_ledger@1", "sequence_id": "synthetic-demo", "timeline_id": "fixture-clock",
              "capture_method": "same_frame_dual_output", "provenance": "synthetic fixture generator, not a game capture",
              "candidate_artifact_sha256": artifact["sha256"], "pairs": pairs}
    save(root / "ledger.json", ledger)
    for role in frames:
        manifest = {"schema": "phonefarm/paired_frame_sequence@1", "protocol_id": rules["protocol_id"],
                    "rules_sha256": digest(root / "rules.json"), "sequence_id": "synthetic-demo", "timeline_id": "fixture-clock",
                    "role": role, "captured_at_unix_ms": 200, "color_space": "sRGB8", "content_scope": "scene_only",
                    "source": {"kind": "synthetic_fixture", "provenance": "examples/sequence_quality_fixture.py",
                               "capture_method": "fixture generator", "device": None, "package": None,
                               "game_version": None, "derivation": None},
                    "pairing_evidence": {"file": "ledger.json", "sha256": digest(root / "ledger.json")}, "frames": frames[role]}
        if role == "candidate":
            manifest["candidate_artifact"] = artifact
        save(root / f"{role}.json", manifest)
    print(f"Synthetic {args.mode} fixture: {root}. No real-game qualification or ghost-free claim.")


if __name__ == "__main__":
    main()
