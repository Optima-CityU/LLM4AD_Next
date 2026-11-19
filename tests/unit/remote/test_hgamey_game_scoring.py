"""Verify frozen game fitness policies with synthetic measurements only."""

import copy
import json
import math

import pytest

from llm4ad.gateway.examples.hgamey.scoring import DEFAULT_GAME_SCORING, normalize_game_scoring, score_game

pytestmark = pytest.mark.unit


@pytest.fixture
def report():
    """Provide equal A/A measurements and official-shaped self-noise references."""
    return {
        "schema": "llm4ad/hgamey-game-ab@1",
        "metrics": {
            f"{side}_{key}": value
            for side in ("baseline", "comparison")
            for key, value in (("display_fps", 30), ("display_frame_p95_upper_ms", 40), ("gpu_uid_active_pct", 60))
        },
        "quality": {"status": "measured", "metrics": [
            {"key": "stable_psnr_db", "better": "higher", "value": 45, "native_self": 45, "ref_line": 44},
            {"key": "stable_dssim", "better": "lower", "value": 0.001, "native_self": 0.001, "ref_line": 0.002},
            {"key": "detail_log2", "better": "near_zero", "value": 0, "native_self": 0.02, "ref_line": 0.17},
        ]},
    }


def only(name, **overrides):
    """Enable exactly one scoring component in a fully frozen policy."""
    return normalize_game_scoring({"weights": {key: float(key == name) for key in DEFAULT_GAME_SCORING["weights"]}, **overrides})


@pytest.fixture
def quality_policy():
    """Provide an explicit policy that scores both performance and official IQ."""
    return normalize_game_scoring({"weights": {"fps": 0.25, "frame_p95": 0.35, "gpu_per_frame": 0.20, "quality": 0.20}})


def test_default_policy_identity_and_copies():
    """Normalizing a frozen policy preserves values without aliasing defaults."""
    policy = normalize_game_scoring(None)
    assert policy == DEFAULT_GAME_SCORING
    assert normalize_game_scoring(policy) == policy
    assert json.loads(json.dumps(policy)) == policy
    policy["sources"]["gpu_per_frame"]["activity"] = "other_activity"
    assert DEFAULT_GAME_SCORING["sources"]["gpu_per_frame"]["activity"] == "gpu_uid_active_pct"


def test_default_policy_has_fixed_performance_and_quality_weights():
    """Freeze all four original weights and a separate automatic quality penalty."""
    expected = {"fps": 0.25, "frame_p95": 0.35, "gpu_per_frame": 0.20, "quality": 0.20}
    assert DEFAULT_GAME_SCORING["weights"] == expected
    assert normalize_game_scoring(None)["weights"] == expected
    assert DEFAULT_GAME_SCORING["quality_gap_score"] == -100.0


def test_quality_gap_score_is_not_normalized_with_weights():
    """A scalar quality cap remains frozen when component weights are normalized."""
    policy = normalize_game_scoring({"weights": {"fps": 25, "frame_p95": 35, "gpu_per_frame": 20, "quality": 20},
                                     "quality_gap_score": -25.0})
    assert policy["weights"] == pytest.approx(DEFAULT_GAME_SCORING["weights"])
    assert policy["quality_gap_score"] == -25.0
    assert normalize_game_scoring(policy) == policy
    assert json.loads(json.dumps(policy, allow_nan=False)) == policy


def test_default_performance_contributions_keep_fixed_weights(report):
    """Ten, twenty and thirty percent gains retain their fixed contributions."""
    report["metrics"].update(comparison_display_fps=33, comparison_display_frame_p95_upper_ms=32, comparison_gpu_uid_active_pct=46.2)
    result = score_game(report, normalize_game_scoring(None))
    assert result["score"] == pytest.approx(1.55)
    assert result["unpenalized_score"] == result["score"]
    assert result["missing"] == []
    assert {name: component["weight"] for name, component in result["components"].items()} == {
        "fps": 0.25, "frame_p95": 0.35, "gpu_per_frame": 0.20, "quality": 0.20,
    }
    assert {name: component["contribution"] for name, component in result["components"].items()} == pytest.approx({
        "fps": 0.25, "frame_p95": 0.70, "gpu_per_frame": 0.60, "quality": 0,
    })


@pytest.mark.parametrize("quality", ["missing", "not_measured", "error", "missing_status", "partial", "nonfinite", "degraded"])
def test_default_quality_gaps_automatically_receive_low_scores(report, quality):
    """Unverified or degraded IQ lowers fitness while preserving weights and diagnostics."""
    report["metrics"]["comparison_display_frame_p95_upper_ms"] = 36
    policy = normalize_game_scoring(None)
    assert score_game(report, policy)["score"] == pytest.approx(0.35)
    if quality == "missing":
        del report["quality"]
    elif quality in ("not_measured", "error"):
        report["quality"].update(status=quality, not_computed=["synthetic missing measurement"])
    elif quality == "partial":
        del report["quality"]["metrics"][0]["native_self"]
    elif quality == "missing_status":
        del report["quality"]["status"]
    else:
        report["quality"]["metrics"][0]["value"] = float("inf") if quality == "nonfinite" else 43
    before = copy.deepcopy(report)
    result = score_game(report, policy)
    assert result["score"] == -100.0
    assessment = result["quality_assessment"]
    assert assessment["status"] == ("beyond_reference" if quality == "degraded" else "unverified")
    assert assessment["penalized"] is True
    assert assessment["penalty_score"] == -100.0
    assert assessment["reasons"]
    if quality == "degraded":
        assert result["unpenalized_score"] == pytest.approx(0.35 - 0.20 / 3)
        assert result["missing"] == []
    else:
        assert result["unpenalized_score"] is None
        assert result["missing"]
        assert result["components"]["quality"]["contribution"] is None
    assert {name: component["weight"] for name, component in result["components"].items()} == policy["weights"]
    assert report == before
    assert policy == DEFAULT_GAME_SCORING
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("weights", [(25, 35, 20, 20), (0, 1, 2, 4), (1e308, 1e308, 0, 0), (0.3, 0.6, 0.9, 1.2)])
def test_weights_normalized_once(weights):
    """Normalize finite positive policies identically for every candidate."""
    policy = normalize_game_scoring({"weights": dict(zip(DEFAULT_GAME_SCORING["weights"], weights, strict=True))})
    assert math.fsum(policy["weights"].values()) == 1
    assert normalize_game_scoring(policy) == policy


@pytest.mark.parametrize("policy", [
    [], {"unknown": 1}, {"schema": "other"}, {"weights": []}, {"weights": {"power": 1}},
    {"weights": dict.fromkeys(DEFAULT_GAME_SCORING["weights"], 0)},
    *({"weights": {"fps": value}} for value in (-1, True, None, float("nan"), float("inf"), 10**400)),
    *({"scales": {"fps": value}} for value in (-1, 0, True, float("nan"), float("inf"))),
    {"quality_scales": {"stable_dssim": 0}}, {"sources": {"fps": ""}},
    {"sources": {"quality": "quality..metrics"}}, {"sources": {"gpu_per_frame": "activity"}},
    *({"quality_gap_score": value} for value in (0, 1, True, None, "-100", {}, [], float("nan"), float("inf"),
                                                float("-inf"), -(10**400))),
])
def test_invalid_policy_rejected(policy):
    """Reject malformed policies before computing any candidate fitness."""
    with pytest.raises(ValueError):
        normalize_game_scoring(policy)


def test_equal_a_a_is_zero(report, quality_policy):
    """Equal real measurements and within-reference quality are neutral."""
    before = copy.deepcopy(report)
    result = score_game(report, quality_policy)
    assert result["score"] == 0
    assert result["unpenalized_score"] == 0
    assert result["quality_assessment"] == {
        "status": "within_reference", "penalized": False, "penalty_score": -100.0, "reasons": [],
    }
    assert result["missing"] == []
    assert all(component["contribution"] == 0 for component in result["components"].values())
    assert report == before


def test_equal_zero_uid_activity_is_neutral(report):
    """A measured zero/zero UID activity A/A comparison has no gain or loss."""
    report["metrics"].update(baseline_gpu_uid_active_pct=0, comparison_gpu_uid_active_pct=0)
    assert score_game(report, normalize_game_scoring(None))["score"] == 0


@pytest.mark.parametrize("name,key,candidate,expected", [
    ("fps", "display_fps", 33, 1), ("fps", "display_fps", 27, -1),
    ("frame_p95", "display_frame_p95_upper_ms", 36, 1), ("frame_p95", "display_frame_p95_upper_ms", 44, -1),
    ("gpu_per_frame", "gpu_uid_active_pct", 54, 1), ("gpu_per_frame", "gpu_uid_active_pct", 66, -1),
])
def test_gain_directions(report, name, key, candidate, expected):
    """Reward higher FPS and lower presentation bounds and UID activity per frame."""
    report["metrics"][f"comparison_{key}"] = candidate
    result = score_game(report, only(name))
    assert result["score"] == pytest.approx(expected)
    assert result["metrics"][name]["gain_pct"] == pytest.approx(expected * 10)


def test_gpu_uses_presented_fps_and_honest_label(report):
    """Increasing FPS at fixed UID active percentage lowers activity per frame."""
    report["metrics"]["comparison_display_fps"] = 60
    result = score_game(report, only("gpu_per_frame"))
    assert result["metrics"]["gpu_per_frame"] == {"baseline": 20, "candidate": 10, "gain_pct": 50}
    assert result["score"] == 5
    assert "UID" in result["components"]["gpu_per_frame"]["label"]


@pytest.mark.parametrize("key,value", [("stable_psnr_db", 43), ("stable_dssim", 0.003), ("detail_log2", 0.32), ("detail_log2", -0.32)])
def test_quality_penalizes_reference_excess_in_both_detail_directions(report, key, value):
    """Both lost detail and excess high frequencies trigger the automatic low score."""
    next(row for row in report["quality"]["metrics"] if row["key"] == key)["value"] = value
    result = score_game(report, only("quality"))
    assert result["score"] == -100.0
    assert result["unpenalized_score"] == pytest.approx(-1 / 3)
    assert result["metrics"]["quality"][key]["penalty"] == pytest.approx(1)
    assert result["quality_assessment"]["status"] == "beyond_reference"
    assert result["quality_assessment"]["penalized"] is True
    assert key in " ".join(result["quality_assessment"]["reasons"])


@pytest.mark.parametrize("value", [math.nextafter(44.0, -math.inf), -0.0])
def test_any_reference_excess_is_automatically_penalized(report, value):
    """Even a finite reference excess below a display rounding unit gets a low score."""
    report["quality"]["metrics"][0]["value"] = value
    result = score_game(report, normalize_game_scoring(None))
    assert result["score"] == -100.0
    assert result["quality_assessment"]["status"] == "beyond_reference"


@pytest.mark.parametrize("value,expected", [(43, -7.0), (0, -44 / 3)])
def test_quality_cap_never_improves_a_worse_raw_score(report, value, expected):
    """Apply the frozen cap to the whole score without raising worse measured fitness."""
    report["quality"]["metrics"][0]["value"] = value
    result = score_game(report, only("quality", quality_gap_score=-7.0))
    assert result["score"] == pytest.approx(expected)
    assert result["unpenalized_score"] == pytest.approx((value - 44) / 3)
    assert result["quality_assessment"]["penalty_score"] == -7.0


def test_missing_quality_uses_custom_frozen_gap_score(report):
    """Unavailable quality uses the configured whole-score penalty without a fake contribution."""
    del report["quality"]
    result = score_game(report, normalize_game_scoring({"quality_gap_score": -7.0}))
    assert result["score"] == -7.0
    assert result["unpenalized_score"] is None
    assert result["quality_assessment"]["penalty_score"] == -7.0
    assert result["components"]["quality"]["contribution"] is None


def test_quality_uses_reference_lines_and_has_no_sharpness_reward(report):
    """Improved IQ numbers or arbitrary sharpness values cannot add a bonus."""
    report["quality"]["metrics"][0]["value"] = 90
    report["quality"]["metrics"][1]["value"] = 0
    report["metrics"].update(baseline_sharp_full=0, comparison_sharp_full=100000)
    assert score_game(report, only("quality"))["score"] == 0
    report["quality"]["metrics"][0].update(value=43, ref_line=42)
    assert score_game(report, only("quality"))["score"] == 0


@pytest.mark.parametrize("field,value", [("value", None), ("native_self", float("nan")), ("ref_line", True), ("better", "lower")])
def test_missing_official_quality_is_not_zero_filled(report, quality_policy, field, value):
    """Incomplete positive-weight IQ yields a low score while keeping its contribution unavailable."""
    report["quality"]["metrics"][0][field] = value
    result = score_game(report, quality_policy)
    assert result["score"] == -100.0
    assert result["unpenalized_score"] is None
    assert result["quality_assessment"]["status"] == "unverified"
    assert "stable_psnr_db" in " ".join(result["missing"])
    assert result["components"]["quality"]["weight"] == 0.2
    assert result["components"]["quality"]["contribution"] is None
    assert {name: component["weight"] for name, component in result["components"].items()} == quality_policy["weights"]


def test_missing_quality_report_with_positive_weight_receives_low_score(report, quality_policy):
    """An absent IQ report automatically lowers fitness without blocking completion."""
    del report["quality"]
    result = score_game(report, quality_policy)
    assert result["score"] == -100.0
    assert result["unpenalized_score"] is None
    assert result["quality_assessment"]["status"] == "unverified"
    assert result["missing"]
    assert result["components"]["quality"]["contribution"] is None
    assert {name: component["weight"] for name, component in result["components"].items()} == quality_policy["weights"]


@pytest.mark.parametrize("key,value", [
    ("comparison_display_fps", None), ("baseline_display_fps", 0), ("comparison_gpu_uid_active_pct", -1),
    ("comparison_display_frame_p95_upper_ms", True), ("comparison_gpu_uid_active_pct", float("inf")),
])
@pytest.mark.parametrize("quality", ["within_reference", "beyond_reference", "missing"])
def test_missing_invalid_positive_weight_performance_has_no_score(report, key, value, quality):
    """Quality penalties never fabricate fitness when required device performance is missing."""
    report["metrics"][key] = value
    if quality == "missing":
        del report["quality"]
    elif quality == "beyond_reference":
        report["quality"]["metrics"][0]["value"] = 43
    policy = normalize_game_scoring(None)
    result = score_game(report, policy)
    assert result["score"] is None
    assert result["unpenalized_score"] is None
    assert result["missing"]
    assert {name: component["weight"] for name, component in result["components"].items()} == {
        name: weight for name, weight in policy["weights"].items() if weight > 0
    }
    json.dumps(result, allow_nan=False)


def test_zero_weight_metrics_are_excluded(report):
    """Missing IQ and GPU data do not matter when their fixed weights are zero."""
    assert score_game({"metrics": {"baseline_display_fps": 30, "comparison_display_fps": 30}}, only("fps"))["score"] == 0
    assert set(score_game(report, only("fps"))["components"]) == {"fps"}


@pytest.mark.parametrize("quality", [None, {"status": "error", "not_computed": ["synthetic missing IQ"]},
                                     {"status": "measured", "metrics": [{"value": float("inf")}]}])
def test_explicit_zero_quality_weight_skips_quality_assessment(report, quality):
    """A zero quality weight excludes IQ validation, diagnostics and automatic penalties."""
    report["quality"] = quality
    report["metrics"]["comparison_display_frame_p95_upper_ms"] = 36
    policy = normalize_game_scoring({"weights": {"quality": 0}})
    result = score_game(report, policy)
    assert result["score"] == pytest.approx(0.4375)
    assert result["unpenalized_score"] == result["score"]
    assert result["missing"] == []
    assert "quality" not in result["components"]
    assert result["quality_assessment"] == {
        "status": "not_scored", "penalized": False, "penalty_score": -100.0, "reasons": [],
    }
    json.dumps(result, allow_nan=False)


def test_p95_source_is_fixed_without_automatic_perfetto_fallback(report):
    """Explicit source selection remains stable across candidates and missing data."""
    report["metrics"].update(baseline_perfetto_frame_p95_ms=20, comparison_perfetto_frame_p95_ms=10)
    assert score_game(report, only("frame_p95"))["score"] == 0
    policy = only("frame_p95", sources={"frame_p95": "perfetto_frame_p95_ms"})
    assert score_game(report, policy)["score"] == 5
    del report["metrics"]["comparison_perfetto_frame_p95_ms"]
    assert score_game(report, policy)["score"] is None


def test_custom_scales_and_weighted_sum_explain_contributions(report):
    """The score equals its explained contributions using frozen user scales."""
    report["metrics"]["comparison_display_fps"] = 33
    policy = normalize_game_scoring({"scales": {"fps": 20}})
    result = score_game(report, policy)
    assert result["components"]["fps"]["contribution"] == pytest.approx(0.125)
    assert result["score"] == pytest.approx(sum(component["contribution"] for component in result["components"].values()))
    assert "power" not in result["components"]


@pytest.mark.parametrize("change", ["status", "not_computed", "overflow", "normalized_overflow"])
def test_partial_or_nonfinite_quality_automatically_receives_finite_low_score(report, quality_policy, change):
    """Incomplete IQ and overflowing quality arithmetic produce serializable low fitness."""
    if change == "status":
        report["quality"]["status"] = "error"
    elif change == "not_computed":
        report["quality"]["not_computed"] = ["synthetic missing measurement"]
    elif change == "overflow":
        report["quality"]["metrics"][1]["value"] = 1e308
    else:
        report["quality"]["metrics"][0]["value"] = 43
        quality_policy["scales"]["quality"] = 5e-324
    result = score_game(report, quality_policy)
    assert result["score"] == -100.0
    assert result["unpenalized_score"] is None
    assert result["quality_assessment"]["status"] == "unverified"
    assert result["quality_assessment"]["penalized"] is True
    assert result["quality_assessment"]["reasons"]
    assert result["missing"]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("key,value", [("comparison_display_fps", float("nan")), ("baseline_display_fps", 5e-324),
                                       ("comparison_gpu_uid_active_pct", float("inf"))])
def test_nonfinite_observations_remain_serializable(report, key, value):
    """No nonfinite observation or derived gain leaks into the returned score object."""
    report["metrics"][key] = value
    result = score_game(report, normalize_game_scoring(None))
    assert result["score"] is None
    assert result["missing"]
    json.dumps(result, allow_nan=False)
