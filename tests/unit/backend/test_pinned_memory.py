from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from llm4ad.memory.pinned_memory import current_or_configured_pinned_ids


def _task(memory: dict) -> SimpleNamespace:
    return SimpleNamespace(input_args={"memory": memory})


def test_missing_runtime_file_falls_back_to_manual_task_configuration(tmp_path: Path) -> None:
    """A missing runtime file uses the manual selection saved with the task."""
    task = _task(
        {
            "retrieval_mode": "manual",
            "pinned_card_ids": ["project-card", "user-card"],
        }
    )

    assert current_or_configured_pinned_ids(task.input_args, tmp_path / "pinned_memory.json") == [
        "project-card",
        "user-card",
    ]


def test_explicit_empty_runtime_file_overrides_task_configuration(tmp_path: Path) -> None:
    """An explicit empty runtime selection clears the configured pins."""
    path = tmp_path / "pinned_memory.json"
    path.write_text(json.dumps({"pinned_card_ids": []}), encoding="utf-8")
    task = _task(
        {
            "retrieval_mode": "manual",
            "pinned_card_ids": ["project-card"],
        }
    )

    assert current_or_configured_pinned_ids(task.input_args, path) == []
