"""Dependency-free presentation and configuration helpers."""

from __future__ import annotations

import ipaddress
import json
import math
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

SETTINGS_FIELDS = (
    "server",
    "name",
    "allowed_users",
    "adapter",
    "evaluator",
    "state_dir",
    "concurrency",
    "cleanup_enabled",
    "cleanup_interval",
    "cleanup_keep_seconds",
    "local_log_keep_seconds",
    "ui_host",
    "ui_port",
    "open_browser",
    "language",
    "theme",
)
PAGES = (("/", "Overview"), ("/tasks", "Tasks"), ("/settings", "Settings"))
STATES = (
    "pending",
    "running",
    "recovery_required",
    "success",
    "failed",
    "cancelled",
    "cancelling",
    "pending_upload",
)
STATE_COLORS = {
    "ready": "positive",
    "running": "primary",
    "connected": "positive",
    "completed": "positive",
    "queued": "info",
    "accepted": "info",
    "starting": "info",
    "checking": "info",
    "paused": "warning",
    "cancel_requested": "warning",
    "cancelling": "warning",
    "unready": "negative",
    "failed": "negative",
    "error": "negative",
    "cancelled": "grey",
    "stopped": "grey",
    "disconnected": "warning",
    "pending": "info",
    "success": "positive",
    "recovery_required": "warning",
    "pending_upload": "info",
    "configuration_required": "warning",
    "draining_for_settings": "warning",
    "Active": "positive",
    "reconnecting": "warning",
}
_PRIVATE_KEYS = ("password", "secret", "token", "api_key", "authorization", "cookie", "credential")


def is_loopback(host: str) -> bool:
    """Recognize literal loopback addresses without resolving untrusted DNS."""
    host = host.strip().strip("[]").lower()
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or bool(getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback)
    except ValueError:
        return False


def dashboard_url(host: str, port: int) -> str:
    """Return a usable URL for local, wildcard, hostname or IPv6 listeners."""
    host = {"0.0.0.0": "127.0.0.1", "::": "::1", "[::]": "::1"}.get(host, host)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


def should_open_browser(
    preference: bool | None,
    *,
    environ: Mapping[str, str] | None = None,
    interactive: bool | None = None,
) -> bool:
    """Open automatically only for an interactive desktop session."""
    if preference is not None:
        return preference
    env = os.environ if environ is None else environ
    interactive = sys.stdin.isatty() and sys.stdout.isatty() if interactive is None else interactive
    desktop = sys.platform in {"darwin", "win32"} or bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))
    return interactive and desktop and not any(env.get(key) for key in ("SSH_CONNECTION", "SSH_TTY", "CI"))


def resolve_language(preference: str, accept_language: str = "") -> str:
    """Choose Chinese or English using the browser's weighted preferences."""
    if preference in {"zh", "en"}:
        return preference
    languages = []
    for index, value in enumerate(accept_language.split(",")):
        parts = value.strip().split(";")
        try:
            quality = float(next((p.strip()[2:] for p in parts[1:] if p.strip().startswith("q=")), "1"))
        except ValueError:
            continue
        if quality > 0:
            languages.append((-quality, index, parts[0].lower()))
    for _, _, language in sorted(languages):
        if language.startswith("zh"):
            return "zh"
        if language.startswith("en"):
            return "en"
    return "en"


def settings_values(settings: Any) -> dict[str, Any]:
    """Select only universal, non-secret settings from a model or mapping."""
    if not isinstance(settings, Mapping):
        settings = {key: getattr(settings, key, None) for key in SETTINGS_FIELDS}
    return {key: settings[key] for key in SETTINGS_FIELDS if key in settings}


def settings_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize form values before controller validation and persistence.

    Args:
        values: Universal settings collected from dashboard inputs.

    Returns:
        A normalized settings patch without unrelated or secret fields.

    Raises:
        ValueError: If a value cannot represent its intended setting.
    """
    result = settings_values(values)
    users = result.get("allowed_users", [])
    if isinstance(users, str):
        users = users.replace(",", "\n").splitlines()
    result["allowed_users"] = list(dict.fromkeys(str(user).strip() for user in users if str(user).strip()))
    for key in ("concurrency", "ui_port"):
        if key in result:
            try:
                number = float(result[key])
            except (TypeError, ValueError):
                raise ValueError(f"Invalid {key}") from None
            if (
                not math.isfinite(number)
                or not number.is_integer()
                or number < (0 if key == "ui_port" else 1)
                or (key == "ui_port" and number > 65535)
            ):
                raise ValueError(f"Invalid {key}")
            result[key] = int(number)
    for key in ("cleanup_interval", "cleanup_keep_seconds", "local_log_keep_seconds"):
        if key in result:
            try:
                result[key] = float(result[key])
            except (TypeError, ValueError):
                raise ValueError(f"Invalid {key}") from None
            if not math.isfinite(result[key]) or result[key] < 0 or (key == "cleanup_interval" and result[key] == 0):
                raise ValueError(f"Invalid {key}")
    for key, choices in (
        ("adapter", {"custom", "example", "hgamey"}),
        ("language", {"auto", "zh", "en"}),
        ("theme", {"auto", "dark", "light"}),
    ):
        if key in result and result[key] not in choices:
            raise ValueError(f"Invalid {key}")
    for key in ("name", "state_dir", "ui_host", "server"):
        if key in result:
            result[key] = str(result[key]).strip()
            if not result[key]:
                raise ValueError(f"Invalid {key}")
    if "server" in result:
        server = urlsplit(result["server"])
        if server.scheme not in {"http", "https"} or not server.hostname or server.username or server.password or server.query:
            raise ValueError("Server must be an HTTP(S) URL without embedded credentials or query parameters")
    for key in ("cleanup_enabled", "open_browser"):
        if key in result and not isinstance(result[key], bool) and not (key == "open_browser" and result[key] is None):
            raise ValueError(f"Invalid {key}")
    if result.get("adapter") == "hgamey":
        result["concurrency"] = 1
    if "evaluator" in result:
        result["evaluator"] = str(result["evaluator"]).strip() if result["evaluator"] else None
    if result.get("adapter") in {"example", "hgamey"}:
        result["evaluator"] = None
    return result


def public_data(value: Any) -> Any:
    """Redact credential fields before rendering structured diagnostic data."""
    if isinstance(value, Mapping):
        return {
            str(key): ("[redacted]" if any(word in str(key).lower() for word in _PRIVATE_KEYS) else public_data(item))
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [public_data(item) for item in value]
    return value


def pretty_json(value: Any) -> str:
    """Render readable Unicode JSON with credential fields removed."""
    return json.dumps(public_data(value), ensure_ascii=False, indent=2, default=str)


def job_identity(job: Mapping[str, Any]) -> tuple[str, str, str]:
    """Extract the binding, job and attempt identifiers from a controller row."""
    inner = job.get("job") if isinstance(job.get("job"), Mapping) else {}
    return (
        str(job.get("binding", job.get("binding_key", ""))),
        str(job.get("job_id", job.get("id", inner.get("id", "")))),
        str(job.get("attempt_id", inner.get("attempt_id", ""))),
    )


def job_url(job: Mapping[str, Any]) -> str:
    """Keep opaque identifiers in encoded query arguments, never local paths."""
    return "/task?" + urlencode(dict(zip(("binding", "job_id", "attempt_id"), job_identity(job), strict=True)))


def short_identifier(value: str) -> str:
    """Keep identifiers recognizable while giving algorithm names visual priority."""
    return value if len(value) <= 16 else f"{value[:8]}…{value[-4:]}"


def artifact_url(binding: str, job_id: str, attempt_id: str, name: str) -> str:
    """Address an artifact only through the controller's attempt-scoped lookup."""
    return (
        "/artifacts/" + quote(name, safe="") + "?" + urlencode({"binding": binding, "job_id": job_id, "attempt_id": attempt_id})
    )


def state_color(state: Any) -> str:
    """Use a neutral badge for unknown states without inventing progress."""
    return STATE_COLORS.get(str(state), "grey")


def format_timestamp(value: Any) -> str:
    """Format recorded timestamps in UTC without implying unobserved activity."""
    if value is None or value == "":
        return "—"
    try:
        stamp = datetime.fromtimestamp(float(value), UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        return str(value)
    return stamp.strftime("%Y-%m-%d %H:%M:%S UTC")


def format_transport_error(value: Any) -> str:
    """Present structured connection observations without Python dictionary syntax."""
    if not isinstance(value, Mapping):
        return str(value)
    parts = [str(value.get("type") or "Error")]
    if value.get("status") is not None:
        parts.append(f"HTTP {value['status']}")
    return " · ".join(parts)


def duration_text(milliseconds: Any, missing: str = "Not recorded") -> str:
    """Format only observed elapsed time without substituting creation timestamps."""
    if not isinstance(milliseconds, int | float) or not math.isfinite(milliseconds) or milliseconds < 0:
        return missing
    seconds = milliseconds / 1000
    if seconds < 1:
        return f"{milliseconds:g} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min {seconds:02d} s"


def observed_duration(detail: Mapping[str, Any]) -> Any:
    """Prefer observed wall time, retaining a recorded evaluator duration for legacy rows."""
    elapsed = detail.get("elapsed_ms")
    return elapsed if elapsed is not None else detail.get("duration_ms")


def value_text(value: Any, missing: str = "Not recorded") -> str:
    """Preserve numeric precision and render compact values for lists and clipboard actions."""
    if value is None or value == "":
        return missing
    if isinstance(value, str):
        return value
    return json.dumps(public_data(value), ensure_ascii=False, default=str)


def phase_text(phase: Any, translate: Any = None) -> str:
    """Describe recorded lifecycle observations without generating a progress estimate."""
    tr = translate or (lambda value: value)
    if isinstance(phase, Mapping):
        if phase.get("failed_stage"):
            return f"{tr('Failed during')} {tr(str(phase['failed_stage']))}"
        operation = phase.get("operation")
        if operation in {"evaluate", "evaluation", "run", "execute"}:
            operation = None
        parts = [operation, phase.get("stage"), phase.get("status")]
        return " · ".join(dict.fromkeys(tr(str(value)).replace("_", " ") for value in parts if value))
    return str(phase or "").replace("_", " ")


def settings_diff(applied: Any, pending: Any) -> list[dict[str, str]]:
    """Project only changed universal settings as readable applied and pending values."""
    before, after = settings_values(applied), settings_values(pending)
    return [
        {"field": key, "applied": value_text(before.get(key), "—"), "pending": value_text(after[key], "—")}
        for key in SETTINGS_FIELDS
        if key in after and before.get(key) != after[key]
    ]


def local_log_url(binding: str, job_id: str, attempt_id: str, source: str, *, purpose: str = "task") -> str:
    """Address only an attempt-fenced local runtime log snapshot."""
    return (
        "/local-logs/"
        + quote(source, safe="")
        + "?"
        + urlencode(
            {
                "binding": binding,
                "job_id": job_id,
                "attempt_id": attempt_id,
                "purpose": purpose,
            }
        )
    )


def legacy_task_target(query: Mapping[str, str]) -> str:
    """Redirect legacy detail links into a specific task-list dialog, retaining all fences."""
    values = {key: query[key] for key in ("binding", "job_id", "attempt_id", "lang", "theme") if key in query}
    kind = query.get("dialog", "info")
    values["dialog"] = kind if kind in {"records", "local", "metrics", "files", "info", "diagnostics"} else "info"
    return "/tasks?" + urlencode(values)
