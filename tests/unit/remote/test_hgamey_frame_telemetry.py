"""Application presentation telemetry fixtures; no device measurements."""

import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[3] / "src/llm4ad/gateway/examples/hgamey/protocol/upstream/selfsr/frame_telemetry.py"
SPEC = importlib.util.spec_from_file_location("hgamey_frame_telemetry", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

# Synthetic fixtures use the complete AOSP TimeStats text histogram layout.
BUCKETS = (*range(35), *range(36, 51, 2), *range(54, 151, 4), *range(200, 1001, 50))


def histogram_text(counts):
    """Build a full synthetic histogram, including empty buckets."""
    return " ".join(f"{bucket}ms={counts.get(bucket, 0)}" for bucket in BUCKETS)


def fixture(uid=123, package="example.game", histogram=None):
    """Build a synthetic TimeStats block with explicit ownership."""
    if histogram is None:
        histogram = histogram_text({16: 5, 33: 90, 50: 5})
    return (
        f"uid = {uid}\nlayerName = SurfaceView[{package}/Main](BLAST)#1\n"
        f"totalFrames = 101\ndroppedFrames = 2\npresent2present histogram is as below:\n{histogram}\n"
    )


def test_presentation_p95_retains_histogram_bounds():
    """Retain the quantized presentation percentile and its non-GPU provenance."""
    result = module.parse_frame_stats(fixture(), "example.game", 123)
    assert result["metrics"] == {"display_frame_p95_lower_ms": 33, "display_frame_p95_upper_ms": 34, "display_dropped_frames": 2}
    assert result["provenance"]["is_gpu_execution_time"] is False


@pytest.mark.parametrize(
    "text",
    [
        fixture(uid=124),
        fixture(package="example.game.extra"),
        fixture() + fixture(),
        fixture(histogram=histogram_text({})),
        fixture(histogram="16ms=0 33ms=0"),
        fixture(histogram="33ms=1 16ms=2"),
        fixture(histogram="33ms=-1"),
        fixture(histogram="33ms=100"),
        fixture(histogram=histogram_text({33: 100}).replace("34ms=0 ", "", 1)),
        fixture(histogram=histogram_text({33: 100}).replace("34ms=0", "35ms=0", 1)),
        fixture(histogram=histogram_text({33: 100}).replace("33ms=100", "33ms=50 33ms=50")),
        fixture(histogram=histogram_text({33: 100}).replace("33ms=100 34ms=0", "34ms=0 33ms=100")),
        "present2present histogram is as below:\n33ms=100\n",
    ],
)
def test_unavailable_ambiguous_or_malformed_samples_are_not_metrics(text):
    """Reject wrong ownership, ambiguity, and unsupported or malformed histograms."""
    assert module.parse_frame_stats(text, "example.game", 123)["metrics"] == {}


@pytest.mark.parametrize(
    ("bucket", "lower", "upper"),
    [(0, 0, 1), (33, 33, 34), (36, 35, 37), (54, 51, 55), (200, 151, 201), (950, 901, 951)],
)
def test_p95_bounds_follow_integer_milliseconds_then_ceiling_bucket(bucket, lower, upper):
    """Cover narrow and wide buckets without treating labels as lower bounds."""
    result = module.parse_frame_stats(fixture(histogram=histogram_text({bucket: 100})), "example.game", 123)
    assert result["metrics"]["display_frame_p95_lower_ms"] == lower
    assert result["metrics"]["display_frame_p95_upper_ms"] == upper


def test_last_bucket_has_no_invented_upper_bound():
    """Include the entire overflow bucket starting after the preceding bucket."""
    result = module.parse_frame_stats(fixture(histogram=histogram_text({33: 1, 1000: 99})), "example.game", 123)
    assert result["metrics"]["display_frame_p95_lower_ms"] == 951
    assert "display_frame_p95_upper_ms" not in result["metrics"]


@pytest.mark.parametrize("second", [histogram_text({16: 5, 33: 90, 50: 5}), histogram_text({200: 100}), ""])
def test_duplicate_histogram_in_one_layer_is_rejected(second):
    """Reject identical, conflicting, and empty duplicate histogram sections."""
    text = fixture() + f"present2present histogram is as below:\n{second}\n"
    result = module.parse_frame_stats(text, "example.game", 123)
    assert result["metrics"] == {}
    assert "histogram" in result["unavailable_reason"]


def test_histogram_cannot_exceed_layer_frame_count():
    """Reject internally contradictory counts instead of producing a percentile."""
    text = fixture().replace("totalFrames = 101", "totalFrames = 2")
    result = module.parse_frame_stats(text, "example.game", 123)
    assert result["metrics"] == {}
    assert "frame count" in result["unavailable_reason"]
