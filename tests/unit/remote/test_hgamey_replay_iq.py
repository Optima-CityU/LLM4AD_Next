"""Verify same-input replay IQ using synthetic PNGs and a mocked local judge."""

import copy
import hashlib
import json
import os
import struct
import subprocess
import zlib
from unittest.mock import Mock

import pytest
from test_hgamey_game_quality import official_fixture, png_fixture

from llm4ad.gateway.examples.hgamey.protocol import game_quality as quality

pytestmark = pytest.mark.unit


def artifact(root, name, data):
    """Write one owned fixture PNG and return its checksum-bound manifest entry."""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"artifact": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


@pytest.fixture
def replay(tmp_path, monkeypatch):
    """Provide two inputs and six separately written synthetic readbacks for each."""
    frames = []
    for index in (1, 2):
        frame_id = f"frame-{index:02d}"
        frames.append(
            {
                "frame_id": frame_id,
                "source": artifact(tmp_path, f"replay/{frame_id}/source.png", png_fixture(index)),
                "baseline": [
                    artifact(tmp_path, f"replay/{frame_id}/baseline-{i}.png", png_fixture(50 + i))
                    for i in range(3)
                ],
                "candidate": [
                    artifact(tmp_path, f"replay/{frame_id}/candidate-{i}.png", png_fixture(60 + i))
                    for i in range(3)
                ],
                "cleanup_confirmed": True,
            }
        )
    process = Mock(
        return_value=subprocess.CompletedProcess([], 0, json.dumps(official_fixture()), "")
    )
    monkeypatch.setattr(quality.subprocess, "run", process)
    return tmp_path, frames, process


def test_replay_invokes_official_iq_per_source_and_retains_original_reports(replay):
    """Separate source inputs cannot share a noise floor, best candidate or raw metric average."""
    root, frames, process = replay
    originals = [official_fixture(), official_fixture()]
    originals[1]["metrics"][0].update(value=47, native_self=50, ref_line=49)
    originals[1]["sheet"] = "# Second input's original official report\n"
    process.side_effect = [
        subprocess.CompletedProcess([], 0, json.dumps(raw), "diagnostic") for raw in originals
    ]
    result = quality.judge_replay(root, frames, "/fixture/phonefarm")
    assert result["protocol"] == "same-input-gpu-replay@1"
    assert result["status"] == "measured"
    assert result["required_frames"] == 2
    assert result["metrics"] == [] and result["not_computed"] == []
    assert process.call_count == 2
    for call, incoming, measured, raw in zip(
        process.call_args_list, frames, result["frames"], originals, strict=True
    ):
        command = call.args[0]
        assert command[:2] == ["/fixture/phonefarm", "iq-judge"]
        assert call.kwargs == {"capture_output": True, "text": True, "timeout": 300}
        for flag, role in (("--ctrl", "baseline"), ("--knob", "candidate")):
            paths = command[command.index(flag) + 1].split(",")
            assert paths == [str(root / item["artifact"]) for item in incoming[role]]
            assert len(paths) == len(set(paths)) == 3
        assert measured["frame_id"] == incoming["frame_id"]
        assert measured["status"] == "measured"
        assert measured["metrics"] == raw["metrics"]
        provenance = measured["provenance"]
        assert provenance["source"] == incoming["source"]
        assert provenance["command"] == command
        assert json.loads((root / provenance["json_artifact"]).read_text()) == raw
        assert (root / provenance["markdown_artifact"]).read_text() == raw["sheet"]
    assert result["frames"][0]["metrics"][0]["ref_line"] == 44
    assert result["frames"][1]["metrics"][0]["ref_line"] == 49
    assert json.loads((root / "quality/iq-judge.json").read_text()) == result
    assert "frame-01" in (root / "quality/iq-judge.md").read_text()
    retained = {item["name"] for item in quality.quality_artifacts(root)}
    assert len(retained) == 10
    assert all(name.startswith("quality/") for name in retained)


def test_identical_hashes_are_valid_when_all_native_output_paths_are_distinct(replay):
    """Deterministic A/A readbacks are not rejected for having identical pixels."""
    root, frames, process = replay
    for frame in frames:
        for role in ("baseline", "candidate"):
            for sample in frame[role]:
                sample.update(artifact(root, sample["artifact"], png_fixture(50)))
    result = quality.judge_replay(root, frames, "/fixture/phonefarm")
    assert result["status"] == "measured"
    assert process.call_count == 2
    hashes = {
        sample["sha256"]
        for frame in frames
        for role in ("baseline", "candidate")
        for sample in frame[role]
    }
    assert len(hashes) == 1


@pytest.mark.parametrize(
    "failure",
    [
        "source_hash",
        "source_size",
        "output_hash",
        "size",
        "size_bool",
        "size_missing",
        "missing",
        "corrupt_png",
        "pixel_count",
        "filter",
        "too_few",
        "duplicate_path",
        "cross_role_path",
        "source_as_readback",
        "escape",
        "absolute",
        "comma",
        "symlink",
        "hardlink",
        "wrong_frame",
        "wrong_source",
    ],
)
def test_invalid_source_or_readback_cannot_reach_official_iq(replay, failure):
    """Incomplete, altered and aliased files cannot become independent GPU readbacks."""
    root, frames, process = replay
    frame = frames[0]
    sample = frame["baseline"][0]
    path = root / sample["artifact"]
    if failure == "source_hash":
        frame["source"]["sha256"] = "0" * 64
    elif failure == "source_size":
        frame["source"]["size"] += 1
    elif failure == "output_hash":
        sample["sha256"] = "0" * 64
    elif failure == "size":
        sample["size"] += 1
    elif failure == "size_bool":
        sample["size"] = True
    elif failure == "size_missing":
        del sample["size"]
    elif failure == "missing":
        path.unlink()
    elif failure == "corrupt_png":
        sample.update(artifact(root, sample["artifact"], b"\x89PNG\r\n\x1a\nnot pixels"))
    elif failure == "pixel_count":
        data = bytearray(png_fixture(10))
        struct.pack_into(">I", data, 16, 2)
        struct.pack_into(">I", data, 29, zlib.crc32(data[12:29]))
        sample.update(artifact(root, sample["artifact"], bytes(data)))
    elif failure == "filter":
        data = png_fixture(10)
        pixels = zlib.compress(b"\x05\x0a")
        chunk = (
            struct.pack(">I", len(pixels))
            + b"IDAT"
            + pixels
            + struct.pack(">I", zlib.crc32(b"IDAT" + pixels))
        )
        sample.update(artifact(root, sample["artifact"], data[:33] + chunk + data[-12:]))
    elif failure == "too_few":
        frame["baseline"].pop()
    elif failure == "duplicate_path":
        frame["baseline"][1] = copy.deepcopy(sample)
    elif failure == "cross_role_path":
        frame["candidate"][0] = copy.deepcopy(sample)
    elif failure == "source_as_readback":
        frame["baseline"][0] = copy.deepcopy(frame["source"])
    elif failure == "escape":
        sample["artifact"] = "../outside.png"
    elif failure == "absolute":
        sample["artifact"] = str(path)
    elif failure == "comma":
        renamed = path.with_name("baseline,other.png")
        path.rename(renamed)
        sample["artifact"] = renamed.relative_to(root).as_posix()
    elif failure in ("symlink", "hardlink"):
        path.unlink()
        other = root / frame["baseline"][1]["artifact"]
        if failure == "symlink":
            path.symlink_to(other)
        else:
            os.link(other, path)
    elif failure == "wrong_frame":
        sample["frame_id"] = "frame-02"
    else:
        sample["source_sha256"] = "0" * 64
    result = quality.judge_replay(root, [frame], "/fixture/phonefarm")
    assert result["status"] in ("not_measured", "error")
    assert result["required_frames"] == len(result["frames"]) == 1
    assert result["frames"][0]["metrics"] == []
    assert result["not_computed"]
    process.assert_not_called()
    assert (root / "quality/iq-judge.json").is_file()


@pytest.mark.parametrize("failure", ["id", "source", "readback"])
def test_cross_frame_identity_reuse_invalidates_both_frames_before_any_call(replay, failure):
    """Preflight every owner before invoking a judge for an earlier manifest entry."""
    root, frames, process = replay
    if failure == "id":
        frames[1]["frame_id"] = frames[0]["frame_id"]
    elif failure == "source":
        frames[1]["source"] = copy.deepcopy(frames[0]["source"])
    else:
        frames[1]["candidate"][0] = copy.deepcopy(frames[0]["baseline"][0])
    result = quality.judge_replay(root, frames, "/fixture/phonefarm")
    assert result["status"] == "not_measured"
    assert result["required_frames"] == 2
    assert all(frame["not_computed"] for frame in result["frames"])
    process.assert_not_called()


def test_explicit_missing_frame_is_retained_and_cannot_improve_overall_status(replay):
    """A failed parent acquisition stays in the required frame count and summary."""
    root, frames, process = replay
    frames[1] = {
        "frame_id": "frame-02",
        "status": "not_measured",
        "metrics": [],
        "not_computed": ["Native replay did not produce candidate outputs"],
    }
    result = quality.judge_replay(root, frames, "/fixture/phonefarm")
    assert result["status"] == "not_measured"
    assert result["required_frames"] == len(result["frames"]) == 2
    assert result["frames"][0]["status"] == "measured"
    assert "Native replay did not produce candidate outputs" in result["frames"][1]["not_computed"]
    assert any("frame-02" in reason for reason in result["not_computed"])
    process.assert_called_once()


def test_official_missing_tiles_do_not_change_reference_rules_or_drop_a_frame(replay):
    """Preserve the official 32-tile insufficiency and each frame's original reference lines."""
    root, frames, process = replay
    raw = official_fixture()
    raw["metrics"][0]["value"] = None
    raw["not_computed"] = ["Only 31 stable tiles; at least 32 required"]
    process.side_effect = [
        subprocess.CompletedProcess([], 0, json.dumps(item), "")
        for item in (official_fixture(), raw)
    ]
    result = quality.judge_replay(root, frames, "/fixture/phonefarm")
    assert result["status"] == "not_measured"
    assert result["required_frames"] == 2
    assert result["frames"][1]["metrics"] == raw["metrics"]
    assert raw["not_computed"][0] in result["frames"][1]["not_computed"]
    assert all(
        "--tile" not in call.args[0] and "--threshold" not in call.args[0]
        for call in process.call_args_list
    )
    assert all(not item["name"].endswith("stderr.txt") for item in quality.quality_artifacts(root))


@pytest.mark.parametrize(
    "failure",
    [
        "tool",
        "timeout",
        "exit",
        "json",
        "missing",
        "duplicate",
        "direction",
        "boolean",
        "infinity",
        "reasons",
    ],
)
def test_replay_tool_and_malformed_measurements_are_never_reported_as_measured(replay, failure):
    """Require every official metric once, with its direction and finite noise references."""
    root, frames, process = replay
    raw = official_fixture()
    if failure == "tool":
        process.side_effect = FileNotFoundError("fixture phonefarm unavailable")
    elif failure == "timeout":
        process.side_effect = subprocess.TimeoutExpired("iq-judge", 300)
    else:
        if failure == "missing":
            raw["metrics"].pop()
        elif failure == "duplicate":
            raw["metrics"].append(copy.deepcopy(raw["metrics"][0]))
        elif failure == "direction":
            raw["metrics"][0]["better"] = "lower"
        elif failure == "boolean":
            raw["metrics"][0]["native_self"] = True
        elif failure == "reasons":
            raw["not_computed"] = [None]
        stdout = "invalid JSON" if failure == "json" else json.dumps(raw)
        if failure == "infinity":
            stdout = stdout.replace('"value": 40', '"value": 1e999')
        process.return_value = subprocess.CompletedProcess(
            [], 2 if failure == "exit" else 0, stdout, "fixture error"
        )
    result = quality.judge_replay(root, frames[:1], "/fixture/phonefarm")
    assert result["status"] in ("not_measured", "error")
    assert result["frames"][0]["status"] != "measured"
    assert result["not_computed"]
    json.dumps(result, allow_nan=False)
    provenance = result["frames"][0]["provenance"]
    assert (root / provenance["json_artifact"]).is_file()
    assert (root / provenance["markdown_artifact"]).is_file()


@pytest.mark.parametrize("frames", [[], None, [None], [{"frame_id": "../escape"}]])
def test_empty_or_malformed_frames_do_not_become_success(tmp_path, monkeypatch, frames):
    """Missing manifests retain explicit diagnostics without any tool execution."""
    process = Mock()
    monkeypatch.setattr(quality.subprocess, "run", process)
    result = quality.judge_replay(tmp_path, frames, "/fixture/phonefarm")
    assert result["status"] == "not_measured"
    assert result["not_computed"]
    process.assert_not_called()


def test_report_directory_symlink_is_an_error_and_cannot_write_outside_evidence(replay, tmp_path):
    """An unsafe output location cannot redirect official or summary report writes."""
    root, frames, process = replay
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (root / "quality").symlink_to(outside, target_is_directory=True)
    result = quality.judge_replay(root, frames[:1], "/fixture/phonefarm")
    assert result["status"] == result["frames"][0]["status"] == "error"
    assert list(outside.iterdir()) == []
    assert quality.quality_artifacts(root) == []
    process.assert_not_called()


def test_source_read_error_is_retained_without_discarding_other_frames(replay, monkeypatch):
    """A filesystem failure stays an error while independent valid frames retain their IQ."""
    root, frames, process = replay
    read_image = quality._image
    failed = root / frames[0]["source"]["artifact"]

    def read(path):
        if path == failed:
            raise PermissionError("Synthetic source read denied")
        return read_image(path)

    monkeypatch.setattr(quality, "_image", read)
    result = quality.judge_replay(root, frames, "/fixture/phonefarm")
    assert result["status"] == "error"
    assert result["required_frames"] == 2
    assert [frame["status"] for frame in result["frames"]] == ["error", "measured"]
    assert any("Synthetic source read denied" in reason for reason in result["not_computed"])
    process.assert_called_once()


def test_native_cleanup_failure_cannot_be_hidden_by_valid_pngs(replay):
    """Native failure provenance prevents otherwise valid files from claiming measured IQ."""
    root, frames, process = replay
    frames[0]["cleanup_confirmed"] = False
    result = quality.judge_replay(root, frames[:1], "/fixture/phonefarm")
    assert result["status"] == "not_measured"
    assert result["required_frames"] == 1
    assert any("cleanup" in reason for reason in result["not_computed"])
    process.assert_not_called()
