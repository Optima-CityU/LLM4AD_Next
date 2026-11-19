"""Verify official game IQ retention using explicit synthetic local fixtures."""

import copy
import hashlib
import json
import struct
import subprocess
import zlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from llm4ad.gateway.examples.hgamey.protocol import game_quality as quality

pytestmark = pytest.mark.unit


def png_fixture(value):
    """Encode a distinct synthetic grayscale PNG for artifact integrity tests."""

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes([0, value])))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def captures(tmp_path):
    """Create three rounds with three explicitly synthetic extra captures per arm."""
    (tmp_path / "shots").mkdir()
    arms = []
    for pair in range(1, 4):
        for side in "AB":
            arm = {
                "arm": f"{side}{pair}",
                "side": side,
                "pair": pair,
                "in_game": True,
                "quality_samples": {"requested": 3, "captures": []},
            }
            for index in range(4):
                suffix = "" if index == 0 else f"_quality_{index}"
                path = tmp_path / "shots" / f"{side}{pair}{suffix}.png"
                path.write_bytes(png_fixture(pair * 20 + (side == "B") * 10 + index))
                if index:
                    arm["quality_samples"]["captures"].append(str(path))
                else:
                    arm["shot"] = str(path)
            arms.append(arm)
    return tmp_path, {"rounds": 3, "quality_samples": 3}, arms


def official_fixture():
    """Return official-shaped metric rows with a measured quality gap."""
    return {
        "metrics": [
            {
                "key": "stable_psnr_db",
                "better": "higher",
                "value": 40,
                "native_self": 45,
                "ref_line": 44,
            },
            {
                "key": "stable_dssim",
                "better": "lower",
                "value": 0.004,
                "native_self": 0.001,
                "ref_line": 0.002,
            },
            {
                "key": "detail_log2",
                "better": "near_zero",
                "value": -0.2,
                "native_self": 0.02,
                "ref_line": 0.17,
            },
        ],
        "not_computed": [],
        "sheet": "| Synthetic official-shaped fixture |\n",
    }


def test_all_rounds_and_extra_images_have_integrity_and_comparisons(captures):
    """Declare all recorded rounds, including the third, without a first-two cap."""
    root, options, arms = captures
    (root / "shots/unrecorded.png").write_bytes(png_fixture(200))
    artifacts, comparisons = quality.screenshot_artifacts(root, options, list(reversed(arms)))
    assert len(artifacts) == 24
    assert len(comparisons) == 12
    by_name = {item["name"]: item for item in artifacts}
    assert "shots/A3.png" in by_name and "shots/B3.png" in by_name
    assert "shots/unrecorded.png" not in by_name
    for item in artifacts:
        data = (root / item["name"]).read_bytes()
        assert item["size"] == len(data)
        assert item["sha256"] == hashlib.sha256(data).hexdigest()
        assert item["media_type"] == "image/png"
    for comparison in comparisons:
        assert comparison["kind"] == "game"
        assert [image["role"] for image in comparison["images"]] == ["baseline", "candidate"]
        assert len({image["artifact"] for image in comparison["images"]}) == 2


@pytest.mark.parametrize(
    "failure", ["missing", "corrupt", "wrong_source", "duplicate_path", "symlink"]
)
def test_invalid_capture_cannot_be_used_or_copied_as_noise(captures, failure, monkeypatch):
    """Missing, corrupt, aliased and repeated paths cannot stand in for new captures."""
    root, options, arms = captures
    options["rounds"] = 1
    arms = arms[:2]
    path = Path(arms[0]["quality_samples"]["captures"][1])
    if failure == "missing":
        path.unlink()
    elif failure == "corrupt":
        path.write_bytes(b"\x89PNG\r\n\x1a\nnot pixels")
    elif failure in ("wrong_source", "duplicate_path"):
        arms[0]["quality_samples"]["captures"][1] = arms[0]["quality_samples"]["captures"][0]
    else:
        path.unlink()
        path.symlink_to(Path(arms[0]["quality_samples"]["captures"][0]))
    process = Mock(side_effect=AssertionError("Incomplete capture sets must not reach IQ"))
    monkeypatch.setattr(quality.subprocess, "run", process)
    result = quality.measure_quality(root, options, arms, "/fixture/phonefarm")
    assert result["status"] == "not_measured"
    assert result["not_computed"]
    process.assert_not_called()
    assert (root / "quality/iq-judge.json").is_file()
    assert "unavailable" in (root / "quality/iq-judge.md").read_text()


def test_official_iq_runs_per_pair_and_retains_original_rows(captures, monkeypatch):
    """Keep three independent A/B noise floors without cross-cold-start pooling."""
    root, options, arms = captures
    raw = official_fixture()
    process = Mock(
        return_value=subprocess.CompletedProcess([], 0, json.dumps(raw), "fixture diagnostic")
    )
    monkeypatch.setattr(quality.subprocess, "run", process)
    result = quality.measure_quality(root, options, arms, "/fixture/phonefarm")
    assert process.call_count == 3
    reports = []
    for pair, (call, frame) in enumerate(
        zip(process.call_args_list, result["frames"], strict=True), 1
    ):
        command = call.args[0]
        assert command[:2] == ["/fixture/phonefarm", "iq-judge"]
        assert frame["frame_id"] == f"round-{pair}"
        assert frame["pair"] == pair
        for flag, role, side in (("--ctrl", "baseline", "A"), ("--knob", "candidate", "B")):
            paths = command[command.index(flag) + 1].split(",")
            assert len(paths) == len(set(paths)) == 3
            assert {Path(path).name for path in paths} == {
                f"{side}{pair}_quality_{i}.png" for i in (1, 2, 3)
            }
            assert len(result["provenance"]["captures"][role]) == 9
        assert frame["metrics"] == raw["metrics"]
        provenance = frame["provenance"]
        assert json.loads((root / provenance["json_artifact"]).read_text()) == raw
        assert (root / provenance["markdown_artifact"]).read_text() == raw["sheet"]
        reports.extend([provenance["json_artifact"], provenance["markdown_artifact"]])
    assert result["status"] == "measured"
    assert result["metrics"] == []
    assert result["required_frames"] == 3
    assert json.loads((root / "quality/iq-judge.json").read_text()) == result
    assert "best candidate per metric" in result["provenance"]["aggregation"]
    artifacts = {item["name"] for item in quality.quality_artifacts(root)}
    assert {"quality/iq-judge.json", "quality/iq-judge.md", *reports} <= artifacts
    assert len(artifacts) == 14


def test_one_missing_pair_cannot_be_hidden_by_complete_other_pairs(captures, monkeypatch):
    """Retain good round reports while refusing an incomplete overall measurement."""
    root, options, arms = captures
    Path(arms[0]["quality_samples"]["captures"][0]).unlink()
    process = Mock(
        return_value=subprocess.CompletedProcess([], 0, json.dumps(official_fixture()), "")
    )
    monkeypatch.setattr(quality.subprocess, "run", process)
    result = quality.measure_quality(root, options, arms, "/fixture/phonefarm")
    assert result["status"] == "not_measured"
    assert result["required_frames"] == 3
    assert [frame["status"] for frame in result["frames"]] == [
        "not_measured",
        "measured",
        "measured",
    ]
    assert process.call_count == 2
    assert any("round-1" in reason for reason in result["not_computed"])


@pytest.mark.parametrize(
    "failure", ["tool", "timeout", "exit", "json", "rows", "missing_row", "missing_noise"]
)
def test_official_failure_and_missing_metrics_remain_explicit(captures, monkeypatch, failure):
    """Tool, transport and IQ insufficiency failures never become measured zero gaps."""
    root, options, arms = captures
    raw = copy.deepcopy(official_fixture())
    if failure == "rows":
        raw["metrics"] = {}
    elif failure == "missing_row":
        raw["metrics"].pop()
    elif failure == "missing_noise":
        raw["metrics"][0]["native_self"] = None
    result = subprocess.CompletedProcess(
        [],
        2 if failure == "exit" else 0,
        "malformed" if failure == "json" else json.dumps(raw),
        "fixture failure",
    )
    process = Mock(return_value=result)
    if failure in ("tool", "timeout"):
        process.side_effect = (
            FileNotFoundError("phonefarm missing")
            if failure == "tool"
            else subprocess.TimeoutExpired("iq", 300)
        )
    monkeypatch.setattr(quality.subprocess, "run", process)
    measured = quality.measure_quality(root, options, arms, "/fixture/phonefarm")
    assert measured["status"] in ("not_measured", "error")
    assert measured["not_computed"]
    json.dumps(measured, allow_nan=False)
    assert (root / "quality/iq-judge.json").exists()
    assert (root / "quality/iq-judge.md").exists()


def test_fewer_than_three_real_captures_never_invokes_official_iq(captures, monkeypatch):
    """One or two distinct samples cannot manufacture a baseline noise floor."""
    root, options, arms = captures
    options.update(rounds=1, quality_samples=2)
    process = Mock()
    monkeypatch.setattr(quality.subprocess, "run", process)
    result = quality.measure_quality(root, options, arms[:2], "/fixture/phonefarm")
    assert result["status"] == "not_measured"
    assert "at least three" in " ".join(result["not_computed"])
    process.assert_not_called()


def test_final_rerolled_arm_keeps_its_actual_capture_paths(captures, monkeypatch):
    """An accepted retry uses its own original screenshots without renaming or copies."""
    root, options, arms = captures
    arm = arms[-1]
    paths = [arm["shot"], *arm["quality_samples"]["captures"]]
    renamed = []
    for path in map(Path, paths):
        target = path.with_name(path.name.replace("B3", "B3#r1"))
        path.rename(target)
        renamed.append(str(target))
    arm["shot"] = renamed[0]
    arm["quality_samples"]["captures"] = renamed[1:]
    monkeypatch.setattr(
        quality.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(official_fixture()), "")),
    )
    measured = quality.measure_quality(root, options, arms, "/fixture/phonefarm")
    assert measured["status"] == "measured"
    assert (
        measured["provenance"]["captures"]["candidate"][-1]["artifact"]
        == "shots/B3#r1_quality_3.png"
    )


def test_empty_official_logs_are_not_declared_as_uploadable_evidence(tmp_path):
    """A successful IQ command's empty stderr cannot invalidate its completed measurement."""
    output = tmp_path / "quality"
    output.mkdir()
    (output / "iq-judge.stderr.txt").write_bytes(b"")
    (output / "iq-judge.stdout.txt").write_bytes(b'{"metrics": []}')
    (output / "iq-judge.json").write_bytes(b'{"metrics": []}')
    (output / "iq-judge.md").write_text("# Official IQ report\n")
    files = quality.quality_artifacts(tmp_path)
    assert {item["name"] for item in files} == {
        "quality/iq-judge.stdout.txt",
        "quality/iq-judge.json",
        "quality/iq-judge.md",
    }
    assert all(item["size"] > 0 for item in files)
