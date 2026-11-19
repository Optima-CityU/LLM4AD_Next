"""Pure battery-supply product fixtures; no device transport or sysfs access."""

import copy
import importlib.util
import math
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[3] / "src/llm4ad/gateway/examples/hgamey/protocol/upstream/selfsr/power_telemetry.py"
SPEC = importlib.util.spec_from_file_location("hgamey_power_telemetry", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

BATTERY = "/sys/class/power_supply/battery/"
USB = "/sys/class/power_supply/usb/online"
METRIC = "battery_supply_vi_product_mw"


def sample(timestamp=20, *, voltage="3813", current="229", status="Discharging", online="0", begin=None):
    """Build the exact collector record shape from raw paths."""
    return {
        "timestamp_ns": timestamp, "read_start_ns": timestamp - 1 if begin is None else begin,
        "raw": {
            BATTERY + "status": status, BATTERY + "voltage_now": voltage,
            BATTERY + "current_now": current, USB: online,
            BATTERY + "temp": "322",
            "/sys/class/power_supply/mtk-battery/current_now": "0",
            "/sys/class/power_supply/mtk-battery/voltage_now": "4000000",
        },
    }


def parse(samples, profile="oplus_milli_positive", start=10, end=100):
    """Parse a fixed synthetic mono_raw window."""
    return module.parse_power_samples(samples, window_start_ns=start, window_end_ns=end, profile=profile)


def test_oplus_uses_milli_units_without_whole_device_or_cell_scaling():
    """Convert confirmed mV/mA while retaining the supply-only scope."""
    result = parse([sample()])
    assert result["metrics"] == {METRIC: pytest.approx(873.177)}
    assert result["errors"] == []
    assert result["provenance"]["scope"] == "battery_supply"
    assert result["provenance"]["voltage_unit"] == "mV"
    assert result["provenance"]["current_unit"] == "mA"
    assert result["provenance"]["discharge_sign"] == "positive_or_zero"
    assert result["provenance"]["is_whole_device_power"] is False
    assert result["provenance"]["is_gpu_power"] is False
    assert result["provenance"]["cell_scaling"] == "none"
    assert result["provenance"]["energy_extrapolation"] is False
    assert "76d6e1e5b8deb0bcd00355d7a9e68638a501d8bc" in result["provenance"]["unit_source"]


def test_linux_micro_negative_has_equivalent_units_and_explicit_sign():
    """Convert uV/uA to the same supply V*I product without a millionfold error."""
    result = parse([sample(voltage="3813000", current="-229000")], "linux_micro_negative")
    assert result["metrics"] == {METRIC: pytest.approx(873.177)}
    assert result["provenance"]["discharge_sign"] == "negative_or_zero"


def test_arithmetic_mean_of_products_retains_cached_reads_without_independence_claim():
    """Count reads and unique pairs separately without weighting by elapsed time."""
    samples = [sample(20), sample(21), sample(90, voltage="3810", current="232")]
    result = parse(samples)
    assert result["metrics"][METRIC] == pytest.approx((873.177 * 2 + 883.92) / 3)
    assert result["coverage"]["included_samples"] == 3
    assert result["coverage"]["valid_samples"] == 3
    assert result["coverage"]["distinct_vi_pairs"] == 2
    assert result["coverage"]["repeated_vi_samples"] == 1
    assert result["coverage"]["independent_hardware_samples"] is None
    assert result["provenance"]["method"] == "arithmetic_sample_mean"
    assert any("cached" in warning for warning in result["warnings"])


@pytest.mark.parametrize("profile", ["oplus_milli_positive", "linux_micro_negative"])
def test_measured_zero_current_is_valid_but_empty_input_never_invents_zero(profile):
    """Distinguish explicit zero current from absent observations."""
    assert parse([sample(current="0")], profile)["metrics"] == {METRIC: 0}
    assert parse([], profile)["metrics"] == {}


@pytest.mark.parametrize("field,value", [
    ("status", "Charging"), ("status", "Full"), ("status", "Unknown"), ("status", None),
    ("online", "1"), ("online", None), ("online", "nan"), ("online", True),
    ("voltage", "0"), ("voltage", "-3813"), ("voltage", "nan"), ("voltage", "inf"),
    ("voltage", "-inf"), ("voltage", "n/a"), ("voltage", True),
    ("current", "-229"), ("current", "nan"), ("current", "inf"), ("current", None),
])
def test_one_bad_in_window_read_invalidates_whole_product(field, value):
    """Never select only favorable reads from a mixed or incomplete window."""
    result = parse([sample(), sample(30, **{field: value})])
    assert result["metrics"] == {}
    assert result["errors"]
    assert result["coverage"]["included_samples"] == 2


def test_linux_positive_current_is_rejected_instead_of_taking_absolute_value():
    """Reject charging-sign current under the selected negative-discharge profile."""
    result = parse([sample(current="229000", voltage="3813000")], "linux_micro_negative")
    assert result["metrics"] == {} and result["errors"]


@pytest.mark.parametrize("path", [BATTERY + "status", BATTERY + "voltage_now", BATTERY + "current_now", USB])
def test_missing_field_does_not_fall_back_to_dummy_supply(path):
    """Require every selected battery field despite plausible alternate nodes."""
    reading = sample()
    reading["raw"].pop(path)
    result = parse([reading])
    assert result["metrics"] == {} and result["errors"]


def test_window_uses_complete_read_bounds_and_excludes_tail():
    """Include the start boundary and exclude crossing reads and the end boundary."""
    samples = [
        sample(5, status="Charging"), sample(10, begin=10),
        sample(11, begin=9, status="Charging"), sample(50),
        sample(100, begin=99, status="Charging"), sample(110, status="Charging"),
    ]
    result = parse(samples)
    assert result["metrics"] == {METRIC: pytest.approx(873.177)}
    assert result["coverage"]["included_samples"] == 2
    assert result["coverage"]["boundary_samples"] == 2
    assert result["coverage"]["outside_samples"] == 2


@pytest.mark.parametrize("samples", [
    [sample(20), sample(20)], [sample(30), sample(20)], [sample(20, begin=21)],
    [{"timestamp_ns": 20, "raw": {}}], [{"read_start_ns": 19, "raw": {}}],
    [{"timestamp_ns": True, "read_start_ns": 0, "raw": {}}], [None], None,
])
def test_invalid_clock_records_never_produce_numeric_output(samples):
    """Reject unknown or inconsistent read windows without guessing timestamps."""
    result = parse(samples)
    assert result["metrics"] == {} and result["errors"]


@pytest.mark.parametrize("start,end", [(10, 10), (10, 9), (-1, 10), (False, 10), (0, math.inf), (0, 10.5)])
def test_invalid_window_is_reported(start, end):
    """Keep malformed window arguments out of metric calculations."""
    result = parse([sample()], start=start, end=end)
    assert result["metrics"] == {} and result["errors"]


def test_disabled_profile_keeps_raw_evidence_without_conversion_or_mutation():
    """Retain raw values even when the caller explicitly disables the product."""
    samples = [sample(status="Charging")]
    original = copy.deepcopy(samples)
    result = parse(samples, "disabled")
    assert result["metrics"] == {} and result["errors"] == []
    assert result["provenance"]["raw_samples"] == original
    samples[0]["raw"][BATTERY + "current_now"] = "changed"
    assert result["provenance"]["raw_samples"] == original


@pytest.mark.parametrize("profile", ["auto", "oplus", None, {}])
def test_unknown_profile_is_never_guessed(profile):
    """Require an explicit recognized unit/sign contract."""
    result = parse([sample()], profile)
    assert result["metrics"] == {} and result["errors"]


def test_overflowing_product_is_rejected():
    """Require a finite result as well as individually finite readings."""
    result = parse([sample(voltage="1e308", current="1e308")])
    assert result["metrics"] == {} and result["errors"]
