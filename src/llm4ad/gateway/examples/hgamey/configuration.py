"""Environment configuration for the combined gateway and executor container."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from llm4ad.gateway.configuration import flag, number
from llm4ad.gateway.sdk.runtime import cleanup_options
from llm4ad.gateway.sdk.storage import persist as persist


def configure(environment: dict, executor, adapter_directory: Path) -> dict:
    """Resolve environment values without contacting the platform or a phone.

    Args:
        environment: Container environment mapping.
        executor: Extracted executor module, used for its existing device validation.
        adapter_directory: Directory containing the bundled adapter and defaults.

    Returns:
        Validated service options and generated adapter settings paths.
    """
    server = environment.get("LLM4AD_SERVER_URL", "").strip().rstrip("/")
    parsed = urlsplit(server)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Set LLM4AD_SERVER_URL to the platform HTTP(S) base URL")
    if parsed.query or parsed.fragment:
        raise ValueError("LLM4AD_SERVER_URL must not contain a query or fragment")
    name = environment.get("LLM4AD_GATEWAY_NAME", "").strip()
    if not name:
        raise ValueError("Set LLM4AD_GATEWAY_NAME")
    allowed = [
        str(uuid.UUID(value)) for value in re.split(r"[,\s]+", environment.get("LLM4AD_ALLOWED_USER_IDS", "").strip()) if value
    ]
    if not allowed:
        raise ValueError("Set LLM4AD_ALLOWED_USER_IDS to one or more platform user UUIDs")
    root = Path(environment.get("HGAMEY_RUNTIME_DIR", "/data/gateway")).resolve()
    devices_file = Path(environment.get("EXECUTOR_CONFIG", "/config/devices.json"))
    generated_devices = None
    device_profiles = {}
    if environment.get("HGAMEY_DEVICES_JSON", "").strip():
        if environment.get("HGAMEY_DEVICE_HOST"):
            raise ValueError("Use HGAMEY_DEVICES_JSON or HGAMEY_DEVICE_HOST, not both")
        entries = json.loads(environment["HGAMEY_DEVICES_JSON"])
        if not isinstance(entries, list) or not entries:
            raise ValueError("HGAMEY_DEVICES_JSON must be a nonempty device array")
        generated_devices = {"version": 1, "devices": []}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Every HGAMEY_DEVICES_JSON entry must be an object")
            item = dict(entry)
            profile = item.pop("device_profile", None)
            item.setdefault("label", item.get("id"))
            item.setdefault("port", 5555)
            item.setdefault("enabled", True)
            generated_devices["devices"].append(item)
            if profile is not None:
                if not isinstance(profile, str) or not profile.strip():
                    raise ValueError("device_profile must be a nonempty string")
                device_profiles[item.get("id")] = profile.strip()
        devices_file = root / "devices.json"
    elif environment.get("HGAMEY_DEVICE_HOST"):
        devices_file = root / "devices.json"
        generated_devices = {
            "version": 1,
            "devices": [
                {
                    "id": environment.get("HGAMEY_DEVICE_ID") or "genshin-phone",
                    "label": environment.get("HGAMEY_DEVICE_LABEL") or name,
                    "host": environment["HGAMEY_DEVICE_HOST"],
                    "port": number(environment, "HGAMEY_DEVICE_PORT", 5555, integer=True, minimum=1),
                    "expected_serial": environment.get("HGAMEY_DEVICE_SERIAL", ""),
                    "enabled": True,
                }
            ],
        }
    if generated_devices is None:
        devices = executor.load_devices(devices_file)
    else:
        with tempfile.TemporaryDirectory(prefix="hgamey-device-validation-") as temporary:
            path = Path(temporary) / "devices.json"
            path.write_text(json.dumps(generated_devices))
            devices = executor.load_devices(path)
    devices = [item for item in devices if item["enabled"]]
    selection = environment.get("HGAMEY_DEVICE_ID")
    if selection:
        devices = [item for item in devices if item["id"] == selection]
    if not devices:
        raise ValueError("Configure an enabled device; HGAMEY_DEVICE_ID must match an enabled entry")
    for device in devices:
        profile = device_profiles.get(device["id"], environment.get("HGAMEY_DEVICE_PROFILE", "").strip())
        if not profile:
            raise ValueError(f"Set device_profile or HGAMEY_DEVICE_PROFILE for {device['id']}")
        device_profiles[device["id"]] = profile
    port = number(environment, "EXECUTOR_PORT", 8767, integer=True, minimum=1)
    if port > 65535:
        raise ValueError("EXECUTOR_PORT must be at most 65535")
    settings = json.loads((adapter_directory / "config/settings.json").read_text(encoding="utf-8"))
    settings.update(
        executor_url=f"http://127.0.0.1:{port}",
        inputs_root=str(root / "staging"),
        executor_inputs_prefix=str(root / "staging"),
        reference_path=environment.get("HGAMEY_REFERENCE_PATH", "/inputs/genshin"),
        compiler=environment.get("HGAMEY_COMPILER", "glslangValidator"),
        spirv_validator=environment.get("HGAMEY_SPIRV_VALIDATOR", "spirv-val"),
        compile_timeout=number(environment, "HGAMEY_COMPILE_TIMEOUT", 60, minimum=0.001),
        poll_interval=number(environment, "HGAMEY_POLL_INTERVAL", 2, minimum=0.001),
        pause_foreground=flag(environment, "HGAMEY_PAUSE_FOREGROUND"),
        min_operator_improvement_pct=number(environment, "HGAMEY_MIN_OPERATOR_IMPROVEMENT_PCT", 1, minimum=1),
    )
    if settings["min_operator_improvement_pct"] >= 100:
        raise ValueError("HGAMEY_MIN_OPERATOR_IMPROVEMENT_PCT must be less than 100")
    if environment.get("HGAMEY_GAME_CHECK_CONFIG"):
        game_config = Path(environment["HGAMEY_GAME_CHECK_CONFIG"])
        game_check = json.loads(game_config.read_text(encoding="utf-8"))
        if not isinstance(game_check, dict):
            raise ValueError("HGAMEY_GAME_CHECK_CONFIG must contain a game-check object")
        game_check.setdefault("builder_path", str(adapter_directory / "compilation/game_layer.py"))
        settings["game_check"] = game_check
    request = settings["request"]
    request["package"] = environment.get("HGAMEY_PACKAGE", "com.miHoYo.Yuanshen")
    request["workload"]["seconds"] = number(environment, "HGAMEY_EVAL_SECONDS", 5, integer=True, minimum=1)
    request["protocol"]["rounds"] = number(environment, "HGAMEY_EVAL_ROUNDS", 8, integer=True, minimum=2)
    settings["evaluation_profiles"] = {
        "standard": {"seconds": request["workload"]["seconds"], "rounds": request["protocol"]["rounds"]},
        "elite": {
            "seconds": number(
                environment,
                "HGAMEY_ELITE_EVAL_SECONDS",
                max(5, request["workload"]["seconds"]),
                integer=True,
                minimum=1,
            ),
            "rounds": number(
                environment,
                "HGAMEY_ELITE_EVAL_ROUNDS",
                max(16, request["protocol"]["rounds"]),
                integer=True,
                minimum=2,
            ),
        },
    }
    request["protocol"]["p_threshold"] = number(environment, "HGAMEY_P_THRESHOLD", 0.01, minimum=0.000001)
    request["conditions"].update(
        cool_c=number(environment, "HGAMEY_COOL_C", 50, minimum=1),
        require_discharging=flag(environment, "HGAMEY_REQUIRE_DISCHARGING"),
        allow_missing_power=flag(environment, "HGAMEY_ALLOW_MISSING_POWER", True),
    )
    if environment.get("HGAMEY_GPU_LEVEL"):
        request["conditions"]["gpu_level"] = number(environment, "HGAMEY_GPU_LEVEL", 0, integer=True)
    if request["conditions"]["require_discharging"] and request["conditions"]["allow_missing_power"]:
        raise ValueError("Required discharge cannot be combined with HGAMEY_ALLOW_MISSING_POWER=true")
    request["payload"].update(
        budget_ms=number(environment, "HGAMEY_BUDGET_MS", 10, minimum=0.001),
        quality_baseline_db=number(environment, "HGAMEY_QUALITY_BASELINE_DB", 30),
    )
    profiles = []
    for device in devices:
        device_settings = {
            **settings,
            "device_id": device["id"],
            "expected_serial": device["expected_serial"],
            "device_profile": device_profiles[device["id"]],
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "settings": device_settings,
                    "profile_version": environment.get("HGAMEY_PROFILE_VERSION", "genshin-sr-v1"),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        profiles.append(
            {
                "device": device,
                "name": name if len(devices) == 1 else f"{name} / {device['label']} ({device['id']})",
                "settings": device_settings,
                "settings_path": root / "config" / f"{fingerprint}.json",
            }
        )
    return {
        "root": root,
        "server": server,
        "name": name,
        "allowed_users": list(dict.fromkeys(allowed)),
        "devices_file": devices_file,
        "profiles": profiles,
        "settings_path": profiles[0]["settings_path"],
        "settings": profiles[0]["settings"],
        "generated_devices": generated_devices,
        "port": port,
        "executor_data": Path(environment.get("EXECUTOR_DATA", "/data")),
        "startup_check": flag(environment, "HGAMEY_STARTUP_CHECK", True),
        "startup_seconds": number(environment, "HGAMEY_STARTUP_CHECK_SECONDS", 1, integer=True, minimum=1),
        "startup_rounds": number(environment, "HGAMEY_STARTUP_CHECK_ROUNDS", 2, integer=True, minimum=2),
        "startup_timeout": number(environment, "HGAMEY_STARTUP_CHECK_TIMEOUT", 1800, minimum=1),
        **cleanup_settings(environment),
    }


def cleanup_settings(environment: dict) -> dict:
    """Translate older case-specific names into the shared SDK cleanup options."""
    values = dict(environment)
    for current, previous in (
        ("LLM4AD_GATEWAY_CLEANUP_ENABLED", "HGAMEY_CLEANUP_ENABLED"),
        ("LLM4AD_GATEWAY_CLEANUP_INTERVAL_SECONDS", "HGAMEY_CLEANUP_INTERVAL_SECONDS"),
    ):
        if current not in values and previous in values:
            values[current] = values[previous]
    values.setdefault("LLM4AD_GATEWAY_EVIDENCE_KEEP_SECONDS", number(environment, "HGAMEY_LOCAL_EVIDENCE_KEEP_HOURS", 0) * 3600)
    return cleanup_options(values)
