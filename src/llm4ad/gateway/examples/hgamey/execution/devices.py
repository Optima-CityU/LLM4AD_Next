"""Wireless device selection and validated executor submissions."""

import copy
import ipaddress
import re
from pathlib import Path

from .contracts import KINDS, NAME
from .storage import read_json


def load_devices(path):
    """Validate operator-owned wireless device identities and endpoints."""
    path = Path(path)
    if path.stat().st_size > 65536:
        raise ValueError("device config exceeds 64 KiB")
    data = read_json(path)
    if set(data) != {"version", "devices"} or data["version"] != 1:
        raise ValueError("expected devices config version 1")
    if not isinstance(data["devices"], list) or not 1 <= len(data["devices"]) <= 128:
        raise ValueError("devices must contain 1..128 entries")
    devices, ids, addresses, serials = [], set(), set(), set()
    for item in data["devices"]:
        if set(item) != {"id", "label", "host", "port", "expected_serial", "enabled"}:
            raise ValueError("device fields: id, label, host, port, expected_serial, enabled")
        if not isinstance(item["id"], str) or not NAME.fullmatch(item["id"]):
            raise ValueError("invalid device id")
        if not isinstance(item["label"], str) or not 1 <= len(item["label"]) <= 128:
            raise ValueError("invalid device label")
        address = ipaddress.ip_address(item["host"])
        if type(item["port"]) is not int or not 1 <= item["port"] <= 65535:
            raise ValueError("invalid adb port")
        if not isinstance(item["expected_serial"], str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", item["expected_serial"]):
            raise ValueError("expected_serial must be the physical ro.serialno")
        if type(item["enabled"]) is not bool:
            raise ValueError("enabled must be boolean")
        endpoint = f"[{address}]:{item['port']}" if address.version == 6 else f"{address}:{item['port']}"
        if item["id"] in ids or endpoint in addresses or item["expected_serial"] in serials:
            raise ValueError("duplicate device id, endpoint or physical serial")
        ids.add(item["id"])
        addresses.add(endpoint)
        serials.add(item["expected_serial"])
        devices.append(dict(item, endpoint=endpoint))
    return devices


def prepare_submission(body, devices):
    """Resolve one enabled device and freeze the requested evaluation options."""
    if not isinstance(body, dict) or set(body) - {"device_id", "operation", "request", "options", "pause_foreground"}:
        raise ValueError("unsupported submission fields")
    selected = body.get("device_id")
    enabled = [d for d in devices if d["enabled"]]
    if selected is None:
        if len(enabled) != 1:
            raise ValueError("select exactly one device_id")
        device = enabled[0]
    else:
        device = next((d for d in enabled if d["id"] == selected), None)
        if device is None:
            raise ValueError("unknown or disabled device_id")
    frozen = copy.deepcopy(body)
    op = frozen.setdefault("operation", "eval")
    if op not in {"probe", "eval", "gray-loadop", "game-ab"}:
        raise ValueError("operation must be probe, eval, gray-loadop or game-ab")
    if type(frozen.get("pause_foreground", False)) is not bool:
        raise ValueError("pause_foreground must be boolean")
    if op == "eval":
        req = frozen.get("request")
        if not isinstance(req, dict) or req.get("version") != 2 or req.get("kind") not in KINDS:
            raise ValueError("request must be a version 2 phonefarm eval request with one kind")
        if req.get("serial") not in (None, device["endpoint"], device["expected_serial"]):
            raise ValueError("request.serial disagrees with selected phone")
        req["serial"] = device["endpoint"]
        if "options" in frozen:
            raise ValueError("eval uses request, not options")
    elif "request" in frozen:
        raise ValueError("request only applies to eval")
    if op == "gray-loadop":
        options = frozen.setdefault("options", {})
        if not isinstance(options, dict) or set(options) - {"frames", "intensity", "timeout_seconds"}:
            raise ValueError("unsupported gray-loadop options")
        # This shipped layer persists live counters every 300 presents; refbench
        # uses _exit, so a shorter run only leaves the initial zero-count marker.
        for key, default, low, high in [("frames", 600, 300, 100000), ("intensity", 2, 1, 64), ("timeout_seconds", 60, 5, 600)]:
            val = options.setdefault(key, default)
            if type(val) is not int or not low <= val <= high:
                raise ValueError(f"gray-loadop {key} must be {low}..{high}; layer counter evidence requires at least 300 frames")
    elif op == "game-ab":
        from llm4ad.gateway.examples.hgamey.protocol import game_ab

        frozen["options"] = game_ab.validate_options(frozen.get("options", {}))
    elif "options" in frozen:
        raise ValueError("options only apply to gray-loadop or game-ab")
    return {"device": copy.deepcopy(device), "submission": frozen}
