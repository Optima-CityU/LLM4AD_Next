"""Pure sandbox/path utilities shared by the agent build runner.

Lifted verbatim from the backend ``chat_tune_runner`` so the agent-build logic can
live in core (importable by both the backend and the CLI) without depending on the
backend package. No backend, FastAPI, or agentscope dependency here — only stdlib.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def resolve_within_sandbox(base: Path, path: str) -> Path | None:
    """Resolve a user/LLM-supplied path against the sandbox root.

    Resolution rules (designed around common LLM path mistakes):

    1. Relative paths (e.g. ``code/x.py`` or ``./code/x.py``) resolve against ``base``.
    2. Absolute paths already prefixed by ``base`` are accepted as-is.
    3. Other absolute paths — including an LLM mistakenly writing ``/code/x.py`` when
       it means a project-internal ``code/x.py`` — are rewritten to relative and
       resolved per rule 1.
    4. If ``..`` still escapes ``base`` after the above, return ``None``.

    This tolerates the LLM not knowing the real mount point while never relaxing the
    guarantee against genuine out-of-bounds access.

    Args:
        base: Sandbox root directory (already resolved).
        path: User/LLM-supplied path.

    Returns:
        The resolved absolute path inside ``base``; ``None`` if it escapes.
    """
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            # Already inside base: accept.
            candidate.resolve().relative_to(base)
            resolved = candidate.resolve()
        except ValueError:
            # Outside base: strip the leading root and treat as relative
            # (tolerate "/code/x.py"-style mistakes).
            rel = Path(*candidate.parts[1:]) if len(candidate.parts) > 1 else Path()
            resolved = (base / rel).resolve()
    else:
        resolved = (base / candidate).resolve()

    if resolved != base and base not in resolved.parents:
        return None
    return resolved


def sse_frame(obj: dict[str, Any]) -> str:
    """Encode an event dict as a single SSE frame."""
    return f"data: {json.dumps(obj, ensure_ascii=False, default=str)}\n\n"


__all__ = ["resolve_within_sandbox", "sse_frame"]
