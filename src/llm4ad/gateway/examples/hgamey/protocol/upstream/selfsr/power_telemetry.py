"""Parse explicit battery-supply V*I profiles without reading or changing a device."""

from __future__ import annotations

import copy
import math

_BATTERY = "/sys/class/power_supply/battery/"
_USB_ONLINE = "/sys/class/power_supply/usb/online"
_METRIC = "battery_supply_vi_product_mw"
_OPLUS_SOURCE = (
    "https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_mt6991/blob/"
    "76d6e1e5b8deb0bcd00355d7a9e68638a501d8bc/"
    "vendor/oplus/kernel/charger/v2/oplus_chg_gki.c#L701-L765"
)
_PROFILES = {
    "linux_micro_negative": {
        "voltage_unit": "uV", "current_unit": "uA", "discharge_sign": "negative_or_zero",
        "current_sign_factor": -1, "product_divisor": 1_000_000_000,
        "formula": "-voltage_now * current_now / 1e9",
        "unit_source": "https://docs.kernel.org/power/power_supply_class.html",
        "sign_source": "Explicit caller-selected negative-discharge profile; not inferred from Linux node names",
    },
    "oplus_milli_positive": {
        "voltage_unit": "mV", "current_unit": "mA", "discharge_sign": "positive_or_zero",
        "current_sign_factor": 1, "product_divisor": 1000,
        "formula": "voltage_now * current_now / 1000",
        "unit_source": _OPLUS_SOURCE,
        "sign_source": "Caller-confirmed OPLUS positive-discharge gauge convention",
    },
}


def _timestamp(value, name: str) -> int:
    """Require an actual nonnegative integer timestamp without rounding."""
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer on CLOCK_MONOTONIC_RAW")
    return value


def _number(value, name: str) -> float:
    """Read a finite raw numeric value without accepting booleans or missing data."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"Missing or invalid {name}")
    try:
        number = float(value)
    except (ValueError, OverflowError) as error:
        raise ValueError(f"Invalid {name}") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def parse_power_samples(samples, *, window_start_ns, window_end_ns, profile) -> dict:
    """Summarize fully contained, explicitly qualified battery-supply reads.

    Args:
        samples: Collector dictionaries with timestamp_ns, read_start_ns and raw
            sysfs paths. Original readings are copied into provenance unchanged.
        window_start_ns: Inclusive private trace marker timestamp on mono_raw.
        window_end_ns: Exclusive private trace marker timestamp on mono_raw.
        profile: disabled, linux_micro_negative or oplus_milli_positive. Selection
            explicitly declares units and discharge sign; no device is guessed.

    Returns:
        Metrics, coverage, provenance, errors and warnings. Every complete read
        inside the window must have Discharging status, USB offline, finite
        positive battery voltage and a current with the declared discharge sign.
        Any invalid included read suppresses the entire numeric result. The sole
        metric is the arithmetic mean battery-supply V*I product in mW, without
        energy extrapolation, cell scaling, or GPU/whole-device power claims.
    """
    result = {
        "metrics": {},
        "coverage": {
            "total_samples": len(samples) if isinstance(samples, list) else 0,
            "included_samples": 0, "valid_samples": 0, "outside_samples": 0,
            "boundary_samples": 0, "distinct_vi_pairs": 0, "repeated_vi_samples": 0,
            "independent_hardware_samples": None,
        },
        "provenance": {
            "schema": "llm4ad/power-telemetry@1", "profile": profile,
            "scope": "battery_supply", "quantity": "voltage_current_product",
            "method": "arithmetic_sample_mean", "unit": "mW",
            "trace_clock": "mono_raw", "window_policy": "fully contained reads in [start, end)",
            "is_gpu_power": False, "is_whole_device_power": False,
            "cell_topology": "unverified", "cell_scaling": "none", "energy_extrapolation": False,
            "source_paths": [_BATTERY + name for name in ("status", "voltage_now", "current_now")] + [_USB_ONLINE],
            "raw_samples": copy.deepcopy(samples),
        },
        "errors": [], "warnings": [],
    }
    coverage, provenance = result["coverage"], result["provenance"]
    try:
        start = _timestamp(window_start_ns, "window_start_ns")
        end = _timestamp(window_end_ns, "window_end_ns")
        if start >= end:
            raise ValueError("window_end_ns must be after window_start_ns")
        coverage.update(window_start_ns=start, window_end_ns=end, window_duration_ns=end - start)
        if not isinstance(samples, list):
            raise ValueError("samples must be a list of collector records")
        if profile == "disabled":
            result["warnings"].append("Power profile disabled; original readings retained without conversion")
            return result
        if not isinstance(profile, str) or profile not in _PROFILES:
            raise ValueError("Unknown power profile; no automatic unit or sign detection")
        settings = _PROFILES[profile]
        provenance.update(settings)
        result["warnings"].append(
            "Driver readings may be cached for about 10 seconds; read count and distinct V/I pairs "
            "do not establish independent hardware samples or a measured hardware update interval"
        )
        products, pairs = [], set()
        previous = -1
        for index, sample in enumerate(samples):
            if not isinstance(sample, dict):
                raise ValueError(f"Sample {index} is not a collector record")
            finish = _timestamp(sample.get("timestamp_ns"), f"sample {index} timestamp_ns")
            begin = _timestamp(sample.get("read_start_ns"), f"sample {index} read_start_ns")
            if begin > finish or finish <= previous:
                raise ValueError("Sample read bounds must be ordered and timestamps strictly increasing")
            previous = finish
            if finish < start or begin >= end:
                coverage["outside_samples"] += 1
                continue
            if not start <= begin <= finish < end:
                coverage["boundary_samples"] += 1
                continue
            coverage["included_samples"] += 1
            try:
                raw = sample.get("raw")
                if not isinstance(raw, dict):
                    raise ValueError("Missing raw sysfs readings")
                status = raw.get(_BATTERY + "status")
                if not isinstance(status, str) or status.strip() != "Discharging":
                    raise ValueError("battery/status must be Discharging for every included read")
                if _number(raw.get(_USB_ONLINE), "usb/online") != 0:
                    raise ValueError("USB must be offline for every included read")
                voltage = _number(raw.get(_BATTERY + "voltage_now"), "battery/voltage_now")
                current = _number(raw.get(_BATTERY + "current_now"), "battery/current_now")
                discharge_current = current * settings["current_sign_factor"]
                if voltage <= 0 or discharge_current < 0:
                    raise ValueError("Require positive voltage and current with the declared discharge sign")
                product = (voltage / settings["product_divisor"]) * discharge_current
                if not math.isfinite(product):
                    raise ValueError("Battery-supply V*I product is not finite")
                products.append(product)
                pairs.add((voltage, current))
            except (ValueError, TypeError, OverflowError) as error:
                result["errors"].append(f"Sample {index}: {error}")
        coverage.update(
            valid_samples=len(products), distinct_vi_pairs=len(pairs),
            repeated_vi_samples=len(products) - len(pairs),
        )
        if coverage["boundary_samples"]:
            result["warnings"].append("Reads crossing a window boundary were excluded whole")
        if not products:
            result["warnings"].append("No qualified complete reads inside the window; product unavailable")
        elif not result["errors"]:
            result["metrics"][_METRIC] = math.fsum(value / len(products) for value in products)
    except (ValueError, TypeError, OverflowError) as error:
        result["errors"].append(str(error))
    return result
