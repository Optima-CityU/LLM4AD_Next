"""Executor protocol identities and typed local failures."""

import re

JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
IDEMPOTENCY_KEY = re.compile(r"[0-9a-f]{64}\Z")
FEATURES = ["idempotent_submit", "job_lookup", "cancel", "game_ab"]
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
KINDS = {"shader", "sysparam", "gray", "whitebox"}


class SubmissionConflictError(ValueError):
    """Indicate that an idempotency key already belongs to another submission."""


class BackendNotStartedError(RuntimeError):
    """Indicate a preflight failure before any device worker was started."""
