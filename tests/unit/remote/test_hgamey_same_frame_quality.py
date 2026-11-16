"""Keep game windows separate from verified same-input GPU quality replay."""

import hashlib
import json
import time

import pytest
from test_hgamey_game_quality import png_fixture

from llm4ad.gateway.examples.hgamey.protocol import game_ab, same_frame_quality

pytestmark = pytest.mark.unit


@pytest.fixture
def game(tmp_path):
    """Create owned synthetic capture receipts without touching a device."""
    evidence = tmp_path / "evidence"
    shots = evidence / "shots"
    shots.mkdir(parents=True)
    arms = []
    for pair in (1, 2):
        for side in "AB":
            path = shots / f"{side}{pair}_quality_1.png"
            path.write_bytes(png_fixture(pair))
            arms.append({"arm": f"{side}{pair}", "side": side, "pair": pair, "in_game": True,
                         "quality_samples": {"captures": [str(path)]}})
    (evidence / "arms.jsonl").write_text("\n".join(json.dumps(arm) for arm in arms))
    (tmp_path / "job.json").write_text(json.dumps({"state": "running"}))
    frozen = {"device": {"endpoint": "192.0.2.10:5555"},
              "submission": {"options": {"rounds": 2, "quality_samples": 1, "quality_replay": {"fixture": True}}}}
    return tmp_path, frozen, arms


def summary(evidence, frames, phonefarm):
    """Observe the frame ledger without replacing native rendering or official IQ in production."""
    return {"frames": frames, "status": "measured", "metrics": [], "not_computed": []}


def test_each_game_pair_uses_its_own_actual_recorded_input(game, monkeypatch):
    """Pass the exact same frozen program pair and one recorded input to each replay."""
    folder, frozen, _ = game
    calls = []

    def replay(directory, source, shaders, serial, **kwargs):
        calls.append((directory, source, shaders, serial))
        assert not kwargs["cancelled"]() and kwargs["timeout"] > 0
        return {"source": {"artifact": "source.png", "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
                "cleanup_confirmed": True, "baseline": [], "candidate": [],
                "artifacts": [{"name": "baseline.png"}], "evidence_comparisons": [{
                    "id": "frame-00", "images": [{"role": "baseline", "artifact": "baseline.png"}]}]}

    monkeypatch.setattr(same_frame_quality.quality_replay, "replay_frame", replay)
    monkeypatch.setattr(same_frame_quality.game_quality, "judge_replay", summary)
    result = same_frame_quality.measure(folder, frozen, deadline=time.monotonic() + 60, phonefarm="fixture")
    assert [source.name for _, source, _, _ in calls] == ["A1_quality_1.png", "A2_quality_1.png"]
    assert all(shaders is frozen["submission"]["options"]["quality_replay"] for _, _, shaders, _ in calls)
    assert result["cleanup_confirmed"] is True
    assert result["quality"]["protocol"] == "same-input-gpu-replay@1"
    assert result["quality"]["required_frames"] == 2
    assert result["artifacts"][0]["name"] == "quality/replay/frame-01/baseline.png"
    assert result["evidence_comparisons"][0]["kind"] == "static"
    assert result["evidence_comparisons"][0]["scope"] == "same-input-gpu-replay@1"


def test_missing_frame_is_retained_instead_of_selecting_only_successful_frames(game, monkeypatch):
    """All required pairs remain visible when one capture is unavailable."""
    folder, frozen, _ = game
    (folder / "evidence/shots/A1_quality_1.png").unlink()
    calls = []

    def replay(directory, source, *args, **kwargs):
        calls.append(source.name)
        return {"cleanup_confirmed": True, "baseline": [], "candidate": []}

    monkeypatch.setattr(same_frame_quality.quality_replay, "replay_frame", replay)
    monkeypatch.setattr(same_frame_quality.game_quality, "judge_replay", summary)
    result = same_frame_quality.measure(folder, frozen, deadline=time.monotonic() + 60, phonefarm="fixture")
    assert calls == ["A2_quality_1.png"]
    assert len(result["frames"]) == 2 and result["frames"][0]["not_computed"]
    assert result["cleanup_confirmed"] is True


@pytest.mark.parametrize("failure", ["unconfirmed", "exception"])
def test_unconfirmed_device_state_stops_following_replays(game, monkeypatch, failure):
    """Unknown external cleanup cannot become a normal low-quality score."""
    folder, frozen, _ = game
    calls = []

    def replay(*args, **kwargs):
        calls.append(1)
        if failure == "exception":
            raise RuntimeError("Native worker disappeared")
        return {"cleanup_confirmed": False, "baseline": [], "candidate": []}

    monkeypatch.setattr(same_frame_quality.quality_replay, "replay_frame", replay)
    monkeypatch.setattr(same_frame_quality.game_quality, "judge_replay", summary)
    result = same_frame_quality.measure(folder, frozen, deadline=time.monotonic() + 60, phonefarm="fixture")
    assert len(calls) == 1 and result["cleanup_confirmed"] is False
    assert result["frames"][1]["not_computed"] == ["Prior replay restoration is unconfirmed"]


def test_cancellation_drains_current_frame_and_prevents_later_dispatch(game, monkeypatch):
    """Stopping the platform task preserves cleanup and never starts the next input frame."""
    folder, frozen, _ = game
    calls = []

    def replay(*args, **kwargs):
        calls.append(1)
        (folder / "job.json").write_text(json.dumps({"state": "cancel_requested"}))
        return {"cleanup_confirmed": True, "baseline": [], "candidate": []}

    monkeypatch.setattr(same_frame_quality.quality_replay, "replay_frame", replay)
    monkeypatch.setattr(same_frame_quality.game_quality, "judge_replay", summary)
    result = same_frame_quality.measure(folder, frozen, deadline=time.monotonic() + 60, phonefarm="fixture")
    assert len(calls) == 1 and result["cleanup_confirmed"] is True
    assert result["frames"][1]["not_computed"]


def test_replay_shader_contract_is_frozen_and_bound_to_injected_sources(tmp_path):
    """Reject changed input bytes before accepting an external GPU replay."""
    replay = {"protocol": "same-input-gpu-replay@1"}
    for name in ("candidate_comp", "baseline_comp", "candidate_spv", "baseline_spv"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        replay[name + "_path"] = str(path)
        replay[name + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    options = game_ab.validate_options({"quality_replay": replay,
        "candidate_source_sha256": replay["candidate_comp_sha256"],
        "baseline_source_sha256": replay["baseline_comp_sha256"]})
    frozen = {"submission": {"options": options}}
    folder = tmp_path / "job"
    folder.mkdir()
    game_ab.freeze_replay(folder, frozen)
    assert len(frozen["input_files"]) == 4
    assert {path.name for path in (folder / "inputs/replay").iterdir()} == {
        "candidate.comp", "baseline.comp", "candidate.spv", "baseline.spv"}
    assert all((folder / item["path"]).read_bytes() for item in frozen["input_files"])
    replay["candidate_comp_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="injected game shaders"):
        game_ab.validate_options({"quality_replay": replay,
            "candidate_source_sha256": options["candidate_source_sha256"],
            "baseline_source_sha256": options["baseline_source_sha256"]})
