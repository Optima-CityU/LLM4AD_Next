"""Subprocess helpers that preserve only explicit phonefarm lease FDs."""

import os
import subprocess


def inherited_fds():
    """Read only the lock descriptors explicitly passed by phonefarm."""
    return tuple(int(fd) for fd in os.environ.get("PF_LOCK_FDS", "").split(",") if fd)


def command(args, timeout=None, pf=False, **kwargs):
    # Only PF children inherit the lease. ADB daemons must never hold task FDs.
    """Run a subprocess while limiting lease inheritance to phonefarm children."""
    return subprocess.run(args, timeout=timeout, pass_fds=inherited_fds() if pf else (), **kwargs)
