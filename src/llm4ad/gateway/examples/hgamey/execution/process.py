"""Subprocess helpers that preserve only explicit phonefarm lease FDs."""

import os
import subprocess


def inherited_fds():
    """Read only the lock descriptors explicitly passed by phonefarm."""
    return tuple(int(fd) for fd in os.environ.get("PF_LOCK_FDS", "").split(",") if fd)


def command(args, timeout=None, pf=False, unmarked=False, owner_directory=None, **kwargs):
    # Only PF children inherit the lease. ADB daemons must never hold task FDs.
    """Run a command through SDK ownership while preserving native lease FDs.

    Args:
        args: Command argument vector.
        timeout: Existing command deadline in seconds.
        pf: Whether this child must inherit the native phone lease.
        unmarked: Whether a shared-service client must omit attempt markers.
        owner_directory: Explicit SDK ownership scope for controller-side cleanup.
        **kwargs: Additional subprocess options.

    Returns:
        The original subprocess completion record.
    """
    if unmarked:
        kwargs["env"] = {key: value for key, value in kwargs.get("env", os.environ).items()
                         if not key.startswith("LLM4AD_PROCESS_")}
    elif owner_directory or os.environ.get("LLM4AD_PROCESS_DIRECTORY"):
        from llm4ad.gateway.sdk.processes import owned_command

        return owned_command(
            args, owner_directory=owner_directory, timeout=timeout, pass_fds=inherited_fds() if pf else (), **kwargs
        )
    return subprocess.run(args, timeout=timeout, pass_fds=inherited_fds() if pf else (), **kwargs)
