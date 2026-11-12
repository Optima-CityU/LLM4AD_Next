"""Wireless ADB transport, device identity and foreground discovery."""

import os
import re

from .process import command


def assert_identity(device, observed):
    """Reject a wireless endpoint whose physical serial does not match."""
    if observed.strip() != device["expected_serial"]:
        raise RuntimeError(
            f"wireless phone identity mismatch: expected {device['expected_serial']}, "
            f"got {observed.strip()!r}; evaluation not started"
        )


def foreground_component(raw, fields):
    """Return one unambiguous component from exact foreground field names."""
    lines = re.findall(r"(?m)^[ \t]*(?:" + "|".join(fields) + r")[ \t]*[:=]([^\r\n]*)", raw)
    components = {
        component
        for line in lines
        for component in re.findall(r"\bu\d+[ \t]+([A-Za-z0-9_.$]+/[A-Za-z0-9_.$]+)(?=[ \t}]|$)", line)
    }
    return next(iter(components)) if len(components) == 1 else None


def adb(*args, timeout=30):
    """Run the configured ADB executable without inheriting device lease FDs.

    Args:
        *args: ADB command arguments, including the selected wireless endpoint.
        timeout: Maximum duration in seconds.

    Returns:
        Decoded standard output from a successful ADB command.
    """
    result = command([os.environ.get("ADB_BIN", "adb"), *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("ADB: " + result.stdout + result.stderr)
    return result.stdout
