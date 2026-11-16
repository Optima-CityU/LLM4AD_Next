"""Score every same-input GPU replay frame without averaging away quality gaps."""

import copy
import json

import pytest

from llm4ad.gateway.examples.hgamey.scoring import DEFAULT_GAME_SCORING, normalize_game_scoring, score_game

pytestmark = pytest.mark.unit
PROTOCOL = "same-input-gpu-replay@1"
METRIC_KEYS = ("stable_psnr_db", "stable_dssim", "detail_log2")


def frame(frame_id):
    """Provide one complete synthetic replay with official self-noise reference rows."""
    return {
        "frame_id": frame_id, "status": "measured", "not_computed": [],
        "provenance": {"input_sha256": frame_id, "fixture": True},
        "metrics": [
            {"key": "stable_psnr_db", "better": "higher", "value": 46, "native_self": 45, "ref_line": 44},
            {"key": "stable_dssim", "better": "lower", "value": 0.001, "native_self": 0.001, "ref_line": 0.002},
            {"key": "detail_log2", "better": "near_zero", "value": 0, "native_self": 0.02, "ref_line": 0.17},
        ],
    }


@pytest.fixture
def report():
    """Supply neutral performance and two independently measured replay frames."""
    return {
        "metrics": {
            f"{side}_{key}": value
            for side in ("baseline", "comparison")
            for key, value in (("display_fps", 30), ("display_frame_p95_upper_ms", 40), ("gpu_uid_active_pct", 60))
        },
        "quality": {"protocol": PROTOCOL, "status": "measured", "metrics": [], "required_frames": 2,
                    "frames": [frame("frame-00"), frame("frame-01")], "not_computed": []},
    }


def test_all_frames_within_reference_preserve_weights_inputs_and_details(report):
    """A valid replay scores neutrally and keeps each frame's reference measurements."""
    policy = normalize_game_scoring(None)
    before, frozen = copy.deepcopy(report), copy.deepcopy(policy)
    result = score_game(report, policy)
    assert result["score"] == result["unpenalized_score"] == 0
    assert result["missing"] == []
    assert result["quality_assessment"]["status"] == "within_reference"
    assert result["quality_assessment"]["penalized"] is False
    assert result["quality_assessment"]["protocol"] == PROTOCOL
    assert result["components"]["quality"]["weight"] == 0.20
    details = result["metrics"]["quality"]
    assert set(details) == {f"frame-{index:02}.{key}" for index in range(2) for key in METRIC_KEYS}
    for source in report["quality"]["frames"]:
        for row in source["metrics"]:
            detail = details[f"{source['frame_id']}.{row['key']}"]
            assert detail["frame_id"] == source["frame_id"]
            assert detail["status"] == "measured"
            assert detail["native_self"] == row["native_self"]
            assert detail["ref_line"] == row["ref_line"]
            assert detail["penalty"] == 0
    assert report == before and policy == frozen == DEFAULT_GAME_SCORING


def test_one_bad_frame_cannot_be_hidden_by_a_better_frame(report):
    """Averaging raw PSNR would hide this excess; averaging frame penalties must retain it."""
    report["quality"]["frames"][1]["metrics"][0]["value"] = 43
    result = score_game(report, normalize_game_scoring(None))
    assert result["score"] == -100
    assert result["components"]["quality"]["gain"] == pytest.approx(-1 / 6)
    assert result["unpenalized_score"] == pytest.approx(-0.20 / 6)
    assert result["metrics"]["quality"]["frame-00.stable_psnr_db"]["penalty"] == 0
    assert result["metrics"]["quality"]["frame-01.stable_psnr_db"]["penalty"] == 1
    assert result["quality_assessment"]["status"] == "beyond_reference"
    assert "frame-01.stable_psnr_db" in " ".join(result["quality_assessment"]["reasons"])
    assert "frame" in result["components"]["quality"]["formula"]


def test_average_uses_each_frames_normalized_reference_penalties(report):
    """Average the two frame means using their own reference lines and frozen scales."""
    bad = report["quality"]["frames"][1]["metrics"]
    bad[0].update(value=41, ref_line=42)
    bad[1].update(value=0.006, ref_line=0.002)
    bad[2].update(value=-0.77, ref_line=0.17)
    policy = normalize_game_scoring({"quality_scales": {"stable_psnr_db": 0.5, "stable_dssim": 0.002, "detail_log2": 0.3},
                                     "scales": {"quality": 2}})
    result = score_game(report, policy)
    assert result["components"]["quality"]["gain"] == pytest.approx(-1)
    assert result["components"]["quality"]["normalized"] == pytest.approx(-0.5)
    assert result["unpenalized_score"] == pytest.approx(-0.1)
    assert result["score"] == -100
    assert len(result["metrics"]["quality"]) == 6
    report["quality"]["frames"].reverse()
    assert score_game(report, policy) == result


@pytest.mark.parametrize("change", ["missing_frames", "empty_frames", "invalid_frames", "missing_frame", "missing_status",
                                    "not_measured", "frame_errors", "invalid_errors", "missing_errors", "partial_metrics", "duplicate_metric",
                                    "extra_metric", "invalid_metric", "nonfinite", "overflow", "top_status", "top_errors",
                                    "invalid_top_errors", "missing_top_errors", "missing_protocol", "invalid_protocol"])
def test_any_incomplete_frame_or_replay_is_unverified_and_gets_low_score(report, change):
    """Every required frame must be complete even when other frames or top-level rows are valid."""
    quality = report["quality"]
    required = quality["frames"][1]
    quality["metrics"] = copy.deepcopy(frame("legacy-decoy")["metrics"])
    if change == "missing_frames":
        del quality["frames"]
    elif change == "empty_frames":
        quality["frames"] = []
    elif change == "invalid_frames":
        quality["frames"] = {}
    elif change == "missing_frame":
        quality["frames"][1] = None
    elif change == "missing_status":
        del required["status"]
    elif change == "not_measured":
        required["status"] = "not_measured"
    elif change == "frame_errors":
        required["not_computed"] = ["synthetic missing frame measurement"]
    elif change == "invalid_errors":
        required["not_computed"] = None
    elif change == "missing_errors":
        del required["not_computed"]
    elif change == "partial_metrics":
        required["metrics"].pop()
    elif change == "duplicate_metric":
        required["metrics"][2] = copy.deepcopy(required["metrics"][0])
    elif change == "extra_metric":
        required["metrics"].append({"key": "unexpected", "value": 1})
    elif change == "invalid_metric":
        required["metrics"][0]["key"] = []
    elif change == "nonfinite":
        required["metrics"][0]["value"] = float("inf")
    elif change == "overflow":
        required["metrics"][1]["value"] = 1e308
    elif change == "top_status":
        quality["status"] = "error"
    elif change == "top_errors":
        quality["not_computed"] = ["synthetic missing required replay frame"]
    elif change == "invalid_top_errors":
        quality["not_computed"] = False
    elif change == "missing_top_errors":
        del quality["not_computed"]
    elif change == "missing_protocol":
        del quality["protocol"]
    else:
        quality["protocol"] = "unsupported"
    before = copy.deepcopy(report)
    policy = normalize_game_scoring(None)
    result = score_game(report, policy)
    assert result["score"] == -100
    assert result["unpenalized_score"] is None
    assert result["components"]["quality"]["contribution"] is None
    assert result["quality_assessment"]["status"] == "unverified"
    assert result["quality_assessment"]["penalized"] is True
    assert result["quality_assessment"]["reasons"] and result["missing"]
    assert {key: value["weight"] for key, value in result["components"].items()} == policy["weights"]
    assert report == before and policy == DEFAULT_GAME_SCORING
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("required", [-1, 0, 1, 3, True, None, "2"])
def test_declared_frame_count_must_match_all_required_records(report, required):
    """A malformed or mismatching required frame count cannot silently reduce coverage."""
    report["quality"]["required_frames"] = required
    result = score_game(report, normalize_game_scoring(None))
    assert result["score"] == -100
    assert result["quality_assessment"]["status"] == "unverified"
    assert "required_frames" in " ".join(result["missing"])
    assert len(result["metrics"]["quality"]) == 6


def test_an_omitted_required_frame_cannot_leave_a_valid_subset_score(report):
    """Removing an entire required frame invalidates quality without discarding other details."""
    report["quality"]["frames"].pop()
    result = score_game(report, normalize_game_scoring(None))
    assert result["score"] == -100
    assert result["quality_assessment"]["status"] == "unverified"
    assert "required_frames" in " ".join(result["missing"])
    assert set(result["metrics"]["quality"]) == {f"frame-00.{key}" for key in METRIC_KEYS}


@pytest.mark.parametrize("frame_id", [None, "", " ", 1, True, [], "frame-00"])
def test_invalid_or_duplicate_frame_identity_is_not_silently_dropped(report, frame_id):
    """Ambiguous frame identities fail closed while retaining details for both frame records."""
    report["quality"]["frames"][1]["frame_id"] = frame_id
    result = score_game(report, normalize_game_scoring(None))
    assert result["score"] == -100
    assert result["quality_assessment"]["status"] == "unverified"
    assert "frame_id" in " ".join(result["missing"])
    assert len(result["metrics"]["quality"]) == 6
    json.dumps(result, allow_nan=False)


def test_missing_frame_id_is_unverified(report):
    """A missing required identity cannot be synthesized into a valid frame."""
    del report["quality"]["frames"][1]["frame_id"]
    result = score_game(report, normalize_game_scoring(None))
    assert result["score"] == -100
    assert result["quality_assessment"]["status"] == "unverified"
    assert len(result["metrics"]["quality"]) == 6


def test_partial_frame_retains_its_other_measurements_and_the_good_frame(report):
    """One missing row keeps the remaining measured rows available for diagnosis."""
    report["quality"]["frames"][1]["metrics"].pop()
    result = score_game(report, normalize_game_scoring(None))
    details = result["metrics"]["quality"]
    assert len(details) == 6
    assert details["frame-00.stable_psnr_db"]["value"] == 46
    assert details["frame-01.stable_psnr_db"]["value"] == 46
    assert details["frame-01.detail_log2"]["penalty"] is None
    assert details["frame-01.detail_log2"]["status"] == "unverified"


def test_zero_quality_weight_skips_malformed_frame_reports(report):
    """Explicitly disabled quality keeps its legacy skip behavior and performance weights."""
    report["quality"]["frames"] = [None, {"frame_id": [], "metrics": float("inf")}]
    report["metrics"]["comparison_display_frame_p95_upper_ms"] = 36
    policy = normalize_game_scoring({"weights": {"quality": 0}})
    result = score_game(report, policy)
    assert result["score"] == pytest.approx(0.4375)
    assert result["missing"] == []
    assert "quality" not in result["components"]
    assert result["quality_assessment"] == {
        "status": "not_scored", "penalized": False, "penalty_score": -100.0, "reasons": [],
    }
    json.dumps(result, allow_nan=False)


def test_quality_frames_do_not_fabricate_missing_performance(report):
    """A valid replay cannot supply a required device performance measurement."""
    del report["metrics"]["comparison_display_fps"]
    result = score_game(report, normalize_game_scoring(None))
    assert result["score"] is None
    assert result["quality_assessment"]["status"] == "within_reference"
    assert result["missing"]


def test_legacy_single_quality_rows_remain_compatible(report):
    """Legacy IQ details and default weights remain unchanged without the frame protocol."""
    report["quality"] = {"status": "measured", "metrics": frame("legacy")["metrics"]}
    result = score_game(report, normalize_game_scoring(None))
    assert result["score"] == 0
    assert set(result["metrics"]["quality"]) == set(METRIC_KEYS)
    assert result["quality_assessment"] == {
        "status": "within_reference", "penalized": False, "penalty_score": -100.0, "reasons": [],
    }
