"""Shared gateway URL and authorized-user settings."""

import math
import uuid
from urllib.parse import urlsplit


def validate_server(value: str) -> str:
    """Validate a platform base URL before creating persistent credentials."""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("server must be an HTTP(S) platform base URL without credentials, query or fragment")
    return value.rstrip("/")


def user_ids(values: list[str]) -> list[str]:
    """Normalize repeated or comma-separated UUIDs without discarding explicit sharing."""
    return list(dict.fromkeys(str(uuid.UUID(item.strip())) for value in values for item in value.split(",") if item.strip()))


def flag(environment: dict, key: str, default: bool = False) -> bool:
    """Parse an explicit boolean without treating the string false as true."""
    value = str(environment.get(key, str(default))).strip().lower()
    if value not in {"true", "false", "1", "0"}:
        raise ValueError(f"{key} must be true or false")
    return value in {"true", "1"}


def number(environment: dict, key: str, default, integer: bool = False, minimum=0):
    """Parse a finite duration or protocol value from the environment."""
    value = int(environment.get(key, default)) if integer else float(environment.get(key, default))
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{key} must be finite and at least {minimum}")
    return value
