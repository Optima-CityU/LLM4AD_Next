"""Pure helpers for resolving a task's pinned shared-memory selection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def normalize_pinned_ids(raw: object) -> list[str]:
    """Normalize a JSON/API pinned-id list without accepting other shapes."""
    if not isinstance(raw, list):
        return []
    return [str(card_id) for card_id in raw if str(card_id)]


def _read_runtime_pinned_ids(path: Path) -> list[str] | None:
    """Read a runtime selection, preserving an explicitly empty selection."""
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("pinned_card_ids"), list):
        return None
    return normalize_pinned_ids(data["pinned_card_ids"])


def _configured_pinned_ids(task_input_args: Any) -> list[str]:
    """Return the saved manual-mode selection from a task's input arguments."""
    memory_config = task_input_args.get("memory") if isinstance(task_input_args, dict) else None
    if not isinstance(memory_config, dict) or memory_config.get("retrieval_mode") != "manual":
        return []
    return normalize_pinned_ids(memory_config.get("pinned_card_ids"))


def current_or_configured_pinned_ids(task_input_args: Any, path: Path) -> list[str]:
    """Prefer a live runtime selection, with task configuration as fallback."""
    runtime_ids = _read_runtime_pinned_ids(path)
    return _configured_pinned_ids(task_input_args) if runtime_ids is None else runtime_ids
